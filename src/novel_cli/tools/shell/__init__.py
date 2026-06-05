"""Shell 工具模块。

提供执行 shell 命令的能力，支持前台执行和后台运行两种模式。
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Self, override

import kaos
from kaos import AsyncReadable
from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field, model_validator

from novel_cli.background import TaskView, format_task
from novel_cli.soul.agent import Runtime
from novel_cli.soul.approval import Approval
from novel_cli.soul.toolset import get_current_tool_call_or_none
from novel_cli.tools.display import BackgroundTaskDisplayBlock, ShellDisplayBlock
from novel_cli.tools.utils import ToolResultBuilder, load_desc
from novel_cli.utils.environment import Environment
from novel_cli.utils.subprocess_env import get_noninteractive_env

MAX_FOREGROUND_TIMEOUT = 5 * 60
MAX_BACKGROUND_TIMEOUT = 24 * 60 * 60


class Params(BaseModel):
    """Shell 工具的参数模型。

    Attributes:
        command: 要执行的命令。
        timeout: 命令执行的超时时间（秒）。超时后命令将被终止。
        run_in_background: 是否将命令作为后台任务运行。
        description: 后台任务的简短描述。当 run_in_background=true 时必填。
    """

    command: str = Field(description="The command to execute.")
    timeout: int = Field(
        description=(
            "The timeout in seconds for the command to execute. "
            "If the command takes longer than this, it will be killed."
        ),
        default=60,
        ge=1,
        le=MAX_BACKGROUND_TIMEOUT,
    )
    run_in_background: bool = Field(
        default=False,
        description="Whether to run the command as a background task.",
    )
    description: str = Field(
        default="",
        description=(
            "A short description for the background task. Required when run_in_background=true."
        ),
    )

    @model_validator(mode="after")
    def _validate_background_fields(self) -> Self:
        """验证后台运行相关字段的有效性。

        Returns:
            验证后的 Params 实例。

        Raises:
            ValueError: 当 run_in_background=true 但 description 为空，或
                前台命令超时超过 MAX_FOREGROUND_TIMEOUT 时抛出。
        """
        if self.run_in_background and not self.description.strip():
            raise ValueError("description is required when run_in_background is true")
        if not self.run_in_background and self.timeout > MAX_FOREGROUND_TIMEOUT:
            raise ValueError(
                f"timeout must be <= {MAX_FOREGROUND_TIMEOUT}s for foreground commands; "
                f"use run_in_background=true for longer timeouts (up to {MAX_BACKGROUND_TIMEOUT}s)"
            )
        return self


class Shell(CallableTool2[Params]):
    """Shell 命令执行工具类。

    用于执行 shell 命令，支持前台交互式执行和后台异步执行。

    Attributes:
        name: 工具名称。
        params: 参数类型。
    """

    name: str = "Shell"
    params: type[Params] = Params

    def __init__(self, approval: Approval, environment: Environment, runtime: Runtime):
        """初始化 Shell 工具实例。

        Args:
            approval: 命令审批对象。
            environment: 环境配置对象。
            runtime: 运行时环境对象。
        """
        is_powershell = environment.shell_name == "Windows PowerShell"
        super().__init__(
            description=load_desc(
                Path(__file__).parent / ("powershell.md" if is_powershell else "bash.md"),
                {"SHELL": f"{environment.shell_name} (`{environment.shell_path}`)"},
            )
        )
        self._approval = approval
        self._is_powershell = is_powershell
        self._shell_path = environment.shell_path
        self._runtime = runtime

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行 shell 命令。

        Args:
            params: 命令参数。

        Returns:
            工具执行结果，包含命令输出或错误信息。
        """
        builder = ToolResultBuilder()

        if not params.command:
            return builder.error("Command cannot be empty.", brief="Empty command")

        if params.run_in_background:
            return await self._run_in_background(params)

        result = await self._approval.request(
            self.name,
            "run command",
            f"Run command `{params.command}`",
            display=[
                ShellDisplayBlock(
                    language="powershell" if self._is_powershell else "bash",
                    command=params.command,
                )
            ],
        )
        if not result:
            return result.rejection_error()

        def stdout_cb(line: bytes):
            """stdout 行回调函数。"""
            line_str = line.decode(encoding="utf-8", errors="replace")
            builder.write(line_str)

        def stderr_cb(line: bytes):
            """stderr 行回调函数。"""
            line_str = line.decode(encoding="utf-8", errors="replace")
            builder.write(line_str)

        try:
            exitcode = await self._run_shell_command(
                params.command, stdout_cb, stderr_cb, params.timeout
            )

            if exitcode == 0:
                return builder.ok("Command executed successfully.")
            else:
                return builder.error(
                    f"Command failed with exit code: {exitcode}.",
                    brief=f"Failed with exit code: {exitcode}",
                )
        except TimeoutError:
            return builder.error(
                f"Command killed by timeout ({params.timeout}s)",
                brief=f"Killed by timeout ({params.timeout}s)",
            )

    async def _run_in_background(self, params: Params) -> ToolReturnValue:
        """在后台运行 shell 命令。

        Args:
            params: 命令参数。

        Returns:
            工具执行结果。
        """
        tool_call = get_current_tool_call_or_none()
        if tool_call is None:
            return ToolResultBuilder().error(
                "Background shell requires a tool call context.",
                brief="No tool call context",
            )

        result = await self._approval.request(
            self.name,
            "run background command",
            f"Run background command `{params.command}`",
            display=[
                ShellDisplayBlock(
                    language="powershell" if self._is_powershell else "bash",
                    command=params.command,
                )
            ],
        )
        if not result:
            return result.rejection_error()

        try:
            view = self._runtime.background_tasks.create_bash_task(
                command=params.command,
                description=params.description.strip(),
                timeout_s=params.timeout,
                tool_call_id=tool_call.id,
                shell_name="Windows PowerShell" if self._is_powershell else "bash",
                shell_path=str(self._shell_path),
                cwd=str(self._runtime.session.work_dir),
            )
        except Exception as exc:
            builder = ToolResultBuilder()
            return builder.error(f"Failed to start background task: {exc}", brief="Start failed")

        return self._background_ok(view)

    def _background_ok(self, view: TaskView) -> ToolReturnValue:
        """构建后台任务成功的返回结果。

        Args:
            view: 任务视图对象。

        Returns:
            工具执行结果。
        """
        builder = ToolResultBuilder()
        builder.write(
            "\n".join(
                [
                    format_task(view, include_command=True),
                    "automatic_notification: true",
                    "next_step: You will be automatically notified when it completes.",
                    (
                        "next_step: Use TaskOutput with this task_id for a non-blocking "
                        "status/output snapshot. Only set block=true when you intentionally "
                        "want to wait."
                    ),
                    "next_step: Use TaskStop only if the task must be cancelled.",
                    (
                        "human_shell_hint: For users in the interactive shell, "
                        "the only task-management slash command is /task. "
                        "Do not suggest /task list, /task output, /task stop, or /tasks."
                    ),
                ]
            )
        )
        builder.display(
            BackgroundTaskDisplayBlock(
                task_id=view.spec.id,
                kind=view.spec.kind,
                status=view.runtime.status,
                description=view.spec.description,
            )
        )
        return builder.ok("Background task started", brief=f"Started {view.spec.id}")

    async def _run_shell_command(
        self,
        command: str,
        stdout_cb: Callable[[bytes], None],
        stderr_cb: Callable[[bytes], None],
        timeout: int,
    ) -> int:
        """执行 shell 命令并收集输出。

        Args:
            command: 要执行的命令。
            stdout_cb: stdout 行回调函数。
            stderr_cb: stderr 行回调函数。
            timeout: 超时时间（秒）。

        Returns:
            命令的退出码。

        Raises:
            asyncio.CancelledError: 当任务被取消时抛出。
            TimeoutError: 当命令超时时抛出。
        """
        async def _read_stream(stream: AsyncReadable, cb: Callable[[bytes], None]):
            """异步读取流并调用回调函数。

            Args:
                stream: 可读流对象。
                cb: 行回调函数。
            """
            while True:
                line = await stream.readline()
                if line:
                    cb(line)
                else:
                    break

        process = await kaos.exec(*self._shell_args(command), env=get_noninteractive_env())

        # 立即关闭 stdin，使交互式提示（如 git 密码）获得 EOF
        # 而不是永远等待永远不会到来的输入
        process.stdin.close()

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read_stream(process.stdout, stdout_cb),
                    _read_stream(process.stderr, stderr_cb),
                ),
                timeout,
            )
            return await process.wait()
        except asyncio.CancelledError:
            await process.kill()
            raise
        except TimeoutError:
            await process.kill()
            raise

    def _shell_args(self, command: str) -> tuple[str, ...]:
        """构建 shell 命令的参数列表。

        Args:
            command: 要执行的命令字符串。

        Returns:
            shell 可执行文件和参数的元组。
        """
        if self._is_powershell:
            return (str(self._shell_path), "-command", command)
        return (str(self._shell_path), "-c", command)
