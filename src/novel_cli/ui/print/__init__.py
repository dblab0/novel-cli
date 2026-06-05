"""打印应用模块。

本模块提供 Print 类，实现将 Agent 行为打印输出到控制台的应用程序。
支持文本输入格式（text）和 JSON 流式输入格式（stream-json），
以及文本输出格式和 JSON 流式输出格式。

主要功能：
    - 从 stdin 读取用户输入命令
    - 运行 Soul 并通过可视化模块输出结果
    - 处理各种异常并返回相应的退出码
"""

from __future__ import annotations

import asyncio
import json
import sys
from functools import partial
from pathlib import Path

from kosong.chat_provider import (
    APIConnectionError,
    APIEmptyResponseError,
    APIStatusError,
    APITimeoutError,
    ChatProviderError,
)
from kosong.message import Message
from rich import print

from novel_cli.cli import ExitCode, InputFormat, OutputFormat
from novel_cli.soul import (
    LLMNotSet,
    LLMNotSupported,
    MaxStepsReached,
    RunCancelled,
    Soul,
    run_soul,
)
from novel_cli.soul.novelsoul import NovelSoul
from novel_cli.soul.toolset import ToolCallLoopDetected
from novel_cli.ui.print.visualize import visualize
from novel_cli.utils.logging import logger
from novel_cli.utils.signals import install_sigint_handler


class Print:
    """打印应用类，将 Agent 行为输出到控制台。

    Args:
        soul: 要运行的 Soul 实例。
        input_format: 输入格式，支持 "text" 或 "stream-json"。
        output_format: 输出格式，支持 "text" 或 "stream-json"。
        context_file: 存储上下文的文件路径。
        final_only: 是否仅输出最终的助手消息。

    Attributes:
        soul: 要运行的 Soul 实例。
        input_format: 输入格式。
        output_format: 输出格式。
        context_file: 上下文文件路径。
        final_only: 是否仅输出最终消息。
    """

    def __init__(
        self,
        soul: Soul,
        input_format: InputFormat,
        output_format: OutputFormat,
        context_file: Path,
        *,
        final_only: bool = False,
    ):
        self.soul = soul
        self.input_format: InputFormat = input_format
        self.output_format: OutputFormat = output_format
        self.context_file = context_file
        self.final_only = final_only

    async def run(self, command: str | None = None) -> int:
        """运行 Agent 并处理输入输出。

        从 stdin 读取命令（如果未提供），运行 Soul，
        并通过可视化模块输出结果。支持中断处理和各种异常情况。

        Args:
            command: 可选的初始命令，如果为 None 则从 stdin 读取。

        Returns:
            退出码，表示执行结果（SUCCESS、FAILURE 或 RETRYABLE）。

        Raises:
            LLMNotSet: 未设置 LLM 时抛出。
            LLMNotSupported: LLM 不受支持时抛出。
            ChatProviderError: LLM 提供者错误时抛出。
            MaxStepsReached: 达到最大步数限制时抛出。
            RunCancelled: 用户中断执行时抛出。
        """
        cancel_event = asyncio.Event()

        def _handler():
            logger.debug("SIGINT received.")
            cancel_event.set()

        loop = asyncio.get_running_loop()
        remove_sigint = install_sigint_handler(loop, _handler)

        if command is None and not sys.stdin.isatty() and self.input_format == "text":
            command = sys.stdin.read().strip()
            logger.info("Read command from stdin: {command}", command=command)

        try:
            while True:
                if command is None:
                    if self.input_format == "text":
                        return ExitCode.SUCCESS
                    else:
                        assert self.input_format == "stream-json"
                        command = self._read_next_command()
                        if command is None:
                            return ExitCode.SUCCESS

                if command:
                    logger.info("Running agent with command: {command}", command=command)
                    if self.output_format == "text" and not self.final_only:
                        print(command)
                    runtime = self.soul.runtime if isinstance(self.soul, NovelSoul) else None
                    await run_soul(
                        self.soul,
                        command,
                        partial(visualize, self.output_format, self.final_only),
                        cancel_event,
                        runtime.session.wire_file if runtime else None,
                        runtime,
                    )
                else:
                    logger.info("Empty command, skipping")

                command = None
        except LLMNotSet as e:
            logger.exception("LLM not set:")
            print(str(e))
            return ExitCode.FAILURE
        except LLMNotSupported as e:
            logger.exception("LLM not supported:")
            print(str(e))
            return ExitCode.FAILURE
        except ChatProviderError as e:
            logger.exception("LLM provider error:")
            print(str(e))
            return self._classify_provider_error(e)
        except MaxStepsReached as e:
            logger.warning("Max steps reached: {n_steps}", n_steps=e.n_steps)
            print(str(e))
            return ExitCode.FAILURE
        except RunCancelled:
            logger.error("Interrupted by user")
            print("Interrupted by user")
            return ExitCode.FAILURE
        except ToolCallLoopDetected as e:
            logger.error("工具调用循环检测: {e}", e=e)
            print(str(e), file=sys.stderr)
            return ExitCode.FAILURE
        except BaseException as e:
            logger.exception("Unknown error:")
            print(f"Unknown error: {e}")
            raise
        finally:
            remove_sigint()
        return ExitCode.FAILURE

    _RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    @staticmethod
    def _classify_provider_error(e: ChatProviderError) -> int:
        """将 ChatProviderError 分类为退出码。

        Args:
            e: 要分类的 ChatProviderError 异常。

        Returns:
            退出码，SUCCESS 表示成功，RETRYABLE 表示可重试，FAILURE 表示失败。
        """
        if isinstance(e, (APIConnectionError, APITimeoutError, APIEmptyResponseError)):
            return ExitCode.RETRYABLE
        if isinstance(e, APIStatusError):
            if e.status_code in Print._RETRYABLE_STATUS_CODES:
                return ExitCode.RETRYABLE
            return ExitCode.FAILURE
        return ExitCode.FAILURE

    def _read_next_command(self) -> str | None:
        """从 stdin 读取下一条 JSON 格式的命令。

        以 JSON 流式格式从 stdin 读取消息，提取用户角色的消息文本。
        忽略非用户角色的消息和无效的 JSON 行。

        Returns:
            用户消息的文本内容，如果到达 EOF 则返回 None。
        """
        while True:
            json_line = sys.stdin.readline()
            if not json_line:
                # 到达 EOF
                return None

            json_line = json_line.strip()
            if not json_line:
                # 空行，继续读取下一行
                continue

            try:
                data = json.loads(json_line)
                message = Message.model_validate(data)
                if message.role == "user":
                    return message.extract_text(sep="\n")
                logger.warning(
                    "Ignoring message with role `{role}`: {json_line}",
                    role=message.role,
                    json_line=json_line,
                )
            except Exception:
                logger.warning("Ignoring invalid user message: {json_line}", json_line=json_line)
