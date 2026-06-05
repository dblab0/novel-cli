"""ACP kaos 后端适配模块。

本模块实现 KAOS 后端的 ACP 适配，将文件系统和终端操作路由到 ACP 客户端。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterable, Mapping
from contextlib import suppress
from typing import Literal

import acp
from kaos import AsyncReadable, AsyncWritable, Kaos, KaosProcess, StatResult, StrOrKaosPath
from kaos.local import local_kaos
from kaos.path import KaosPath

# 默认终端输出限制（字节）
_DEFAULT_TERMINAL_OUTPUT_LIMIT = 50_000
# 默认轮询间隔（秒）
_DEFAULT_POLL_INTERVAL = 0.2
# 截断提示文本
_TRUNCATION_NOTICE = "[acp output truncated]\n"


class _NullWritable:
    """空写入器，用于忽略写入操作。

    实现 AsyncWritable 接口但不执行任何实际操作。
    """

    def can_write_eof(self) -> bool:
        """是否可以写入 EOF 标记。

        Returns:
            总是返回 False。
        """
        return False

    def close(self) -> None:
        """关闭写入器。不执行任何操作。"""
        return None

    async def drain(self) -> None:
        """等待缓冲区排空。不执行任何操作。"""
        return None

    def is_closing(self) -> bool:
        """是否正在关闭。总是返回 False。

        Returns:
            总是返回 False。
        """
        return False

    async def wait_closed(self) -> None:
        """等待关闭完成。不执行任何操作。"""
        return None

    def write(self, data: bytes) -> None:
        """写入数据。不执行任何操作。

        Args:
            data: 要写入的字节数据。
        """
        return None

    def writelines(self, data: Iterable[bytes], /) -> None:
        """写入多行数据。不执行任何操作。

        Args:
            data: 要写入的字节数据迭代器。
        """
        return None

    def write_eof(self) -> None:
        """写入 EOF 标记。不执行任何操作。"""
        return None


class ACPProcess:
    """ACP 终端执行的 KAOS 进程适配器。

    将 ACP 终端 API 包装为 KAOS 进程接口。

    Attributes:
        stdin: 标准输入流。
        stdout: 标准输出流。
        stderr: 标准错误流。
    """

    def __init__(
        self,
        client: acp.Client,
        session_id: str,
        terminal_id: str,
        *,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        """初始化 ACP 进程适配器。

        Args:
            client: ACP 客户端连接。
            session_id: ACP 会话 ID。
            terminal_id: 终端 ID。
            poll_interval: 输出轮询间隔（秒）。
        """
        self._client = client
        self._session_id = session_id
        self._terminal_id = terminal_id
        self._poll_interval = poll_interval
        self._stdin = _NullWritable()
        self._stdout = asyncio.StreamReader()
        self._stderr = asyncio.StreamReader()
        self.stdin: AsyncWritable = self._stdin
        self.stdout: AsyncReadable = self._stdout
        # ACP 不单独暴露 stderr；保持 stderr 为空
        self.stderr: AsyncReadable = self._stderr
        self._returncode: int | None = None
        self._last_output = ""
        self._truncation_noted = False
        self._exit_future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._poll_task = asyncio.create_task(self._poll_output())

    @property
    def pid(self) -> int:
        """获取进程 ID。

        ACP 不提供真实的进程 ID，返回 -1。

        Returns:
            总是返回 -1。
        """
        return -1

    @property
    def returncode(self) -> int | None:
        """获取进程返回码。

        Returns:
            进程返回码，如果进程尚未结束则返回 None。
        """
        return self._returncode

    async def wait(self) -> int:
        """等待进程结束。

        Returns:
            进程返回码。
        """
        return await self._exit_future

    async def kill(self) -> None:
        """终止进程。通过 ACP kill_terminal API 实现。"""
        await self._client.kill_terminal(
            session_id=self._session_id,
            terminal_id=self._terminal_id,
        )

    def _feed_output(self, output_response: acp.schema.TerminalOutputResponse) -> None:
        """将输出响应数据注入到输出流。

        Args:
            output_response: 终端输出响应对象。
        """
        output = output_response.output
        reset = output_response.truncated or (
            self._last_output and not output.startswith(self._last_output)
        )
        if reset and self._last_output and not self._truncation_noted:
            self._stdout.feed_data(_TRUNCATION_NOTICE.encode("utf-8"))
            self._truncation_noted = True

        delta = output if reset else output[len(self._last_output) :]
        if delta:
            self._stdout.feed_data(delta.encode("utf-8", "replace"))
        self._last_output = output

    @staticmethod
    def _normalize_exit_code(exit_code: int | None) -> int:
        """规范化退出码。

        Args:
            exit_code: 原始退出码。

        Returns:
            如果退出码为 None 则返回 1，否则返回原始退出码。
        """
        return 1 if exit_code is None else exit_code

    async def _poll_output(self) -> None:
        """轮询终端输出直到进程结束。"""
        exit_task = asyncio.create_task(
            self._client.wait_for_terminal_exit(
                session_id=self._session_id,
                terminal_id=self._terminal_id,
            )
        )
        exit_code: int | None = None
        try:
            while True:
                if exit_task.done():
                    exit_response = exit_task.result()
                    exit_code = exit_response.exit_code
                    break

                output_response = await self._client.terminal_output(
                    session_id=self._session_id,
                    terminal_id=self._terminal_id,
                )
                self._feed_output(output_response)
                if output_response.exit_status:
                    exit_code = output_response.exit_status.exit_code
                    try:
                        exit_response = await exit_task
                        exit_code = exit_response.exit_code or exit_code
                    except Exception:
                        pass
                    break

                await asyncio.sleep(self._poll_interval)

            final_output = await self._client.terminal_output(
                session_id=self._session_id,
                terminal_id=self._terminal_id,
            )
            self._feed_output(final_output)
        except Exception as exc:
            error_note = f"[acp terminal error] {exc}\n"
            self._stdout.feed_data(error_note.encode("utf-8", "replace"))
            if exit_code is None:
                exit_code = 1
        finally:
            if not exit_task.done():
                exit_task.cancel()
                with suppress(Exception):
                    await exit_task
            self._returncode = self._normalize_exit_code(exit_code)
            self._stdout.feed_eof()
            self._stderr.feed_eof()
            if not self._exit_future.done():
                self._exit_future.set_result(self._returncode)
            with suppress(Exception):
                await self._client.release_terminal(
                    session_id=self._session_id,
                    terminal_id=self._terminal_id,
                )


class ACPKaos:
    """通过 ACP 路由支持操作的 KAOS 后端。

    将文件读写和终端操作通过 ACP 客户端路由，
    对于不支持的操作回退到本地 kaos。

    Attributes:
        name: kaos 名称标识符。
    """

    name: str = "acp"

    def __init__(
        self,
        client: acp.Client,
        session_id: str,
        client_capabilities: acp.schema.ClientCapabilities | None,
        fallback: Kaos | None = None,
        *,
        output_byte_limit: int | None = _DEFAULT_TERMINAL_OUTPUT_LIMIT,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        """初始化 ACPKaos。

        Args:
            client: ACP 客户端连接。
            session_id: ACP 会话 ID。
            client_capabilities: ACP 客户端能力。
            fallback: 回退 kaos 实例（可选，默认为 local_kaos）。
            output_byte_limit: 终端输出字节限制。
            poll_interval: 输出轮询间隔（秒）。
        """
        self._client = client
        self._session_id = session_id
        self._fallback = fallback or local_kaos
        fs = client_capabilities.fs if client_capabilities else None
        self._supports_read = bool(fs and fs.read_text_file)
        self._supports_write = bool(fs and fs.write_text_file)
        self._supports_terminal = bool(client_capabilities and client_capabilities.terminal)
        self._output_byte_limit = output_byte_limit
        self._poll_interval = poll_interval

    def pathclass(self):
        """获取路径类。

        Returns:
            回退 kaos 的路径类。
        """
        return self._fallback.pathclass()

    def normpath(self, path: StrOrKaosPath) -> KaosPath:
        """规范化路径。

        Args:
            path: 输入路径。

        Returns:
            规范化后的 KaosPath 对象。
        """
        return self._fallback.normpath(path)

    def gethome(self) -> KaosPath:
        """获取用户主目录路径。

        Returns:
            用户主目录的 KaosPath 对象。
        """
        return self._fallback.gethome()

    def getcwd(self) -> KaosPath:
        """获取当前工作目录路径。

        Returns:
            当前工作目录的 KaosPath 对象。
        """
        return self._fallback.getcwd()

    async def chdir(self, path: StrOrKaosPath) -> None:
        """切换当前工作目录。

        Args:
            path: 目标路径。
        """
        await self._fallback.chdir(path)

    async def stat(self, path: StrOrKaosPath, *, follow_symlinks: bool = True) -> StatResult:
        """获取文件或目录状态信息。

        Args:
            path: 目标路径。
            follow_symlinks: 是否跟随符号链接。

        Returns:
            文件状态信息对象。
        """
        return await self._fallback.stat(path, follow_symlinks=follow_symlinks)

    def iterdir(self, path: StrOrKaosPath) -> AsyncGenerator[KaosPath]:
        """迭代目录内容。

        Args:
            path: 目录路径。

        Returns:
            目录内容的异步生成器。
        """
        return self._fallback.iterdir(path)

    def glob(
        self, path: StrOrKaosPath, pattern: str, *, case_sensitive: bool = True
    ) -> AsyncGenerator[KaosPath]:
        """根据模式匹配查找文件。

        Args:
            path: 搜索起始路径。
            pattern: glob 模式。
            case_sensitive: 是否区分大小写。

        Returns:
            匹配路径的异步生成器。
        """
        return self._fallback.glob(path, pattern, case_sensitive=case_sensitive)

    async def readbytes(self, path: StrOrKaosPath, n: int | None = None) -> bytes:
        """读取文件字节内容。

        Args:
            path: 文件路径。
            n: 要读取的字节数（可选）。

        Returns:
            文件字节内容。
        """
        return await self._fallback.readbytes(path, n=n)

    async def readtext(
        self,
        path: StrOrKaosPath,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> str:
        """读取文件文本内容。

        如果客户端支持文件读取，通过 ACP read_text_file API 实现；
        否则回退到本地 kaos。

        Args:
            path: 文件路径。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            文件文本内容。
        """
        abs_path = self._abs_path(path)
        if not self._supports_read:
            return await self._fallback.readtext(abs_path, encoding=encoding, errors=errors)
        response = await self._client.read_text_file(path=abs_path, session_id=self._session_id)
        return response.content

    async def readlines(
        self,
        path: StrOrKaosPath,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> AsyncGenerator[str]:
        """逐行读取文件内容。

        Args:
            path: 文件路径。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            文件行的异步生成器。
        """
        text = await self.readtext(path, encoding=encoding, errors=errors)
        for line in text.splitlines(keepends=True):
            yield line

    async def writebytes(self, path: StrOrKaosPath, data: bytes) -> int:
        """写入文件字节内容。

        Args:
            path: 文件路径。
            data: 要写入的字节数据。

        Returns:
            写入的字节数。
        """
        return await self._fallback.writebytes(path, data)

    async def writetext(
        self,
        path: StrOrKaosPath,
        data: str,
        *,
        mode: Literal["w", "a"] = "w",
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> int:
        """写入文件文本内容。

        如果客户端支持文件写入，通过 ACP write_text_file API 实现；
        否则回退到本地 kaos。

        Args:
            path: 文件路径。
            data: 要写入的文本数据。
            mode: 写入模式（"w" 为覆盖，"a" 为追加）。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            写入的字符数。
        """
        abs_path = self._abs_path(path)
        if mode == "a":
            if self._supports_read and self._supports_write:
                existing = await self.readtext(abs_path, encoding=encoding, errors=errors)
                await self._client.write_text_file(
                    path=abs_path,
                    content=existing + data,
                    session_id=self._session_id,
                )
                return len(data)
            return await self._fallback.writetext(
                abs_path, data, mode="a", encoding=encoding, errors=errors
            )

        if not self._supports_write:
            return await self._fallback.writetext(
                abs_path, data, mode=mode, encoding=encoding, errors=errors
            )

        await self._client.write_text_file(
            path=abs_path,
            content=data,
            session_id=self._session_id,
        )
        return len(data)

    async def mkdir(
        self, path: StrOrKaosPath, parents: bool = False, exist_ok: bool = False
    ) -> None:
        """创建目录。

        Args:
            path: 目录路径。
            parents: 是否创建父目录。
            exist_ok: 是否允许目录已存在。
        """
        await self._fallback.mkdir(path, parents=parents, exist_ok=exist_ok)

    async def exec(self, *args: str, env: Mapping[str, str] | None = None) -> KaosProcess:
        """执行命令。

        Args:
            args: 命令和参数。
            env: 环境变量映射（可选）。

        Returns:
            KAOS 进程对象。
        """
        return await self._fallback.exec(*args, env=env)

    def _abs_path(self, path: StrOrKaosPath) -> str:
        """将路径转换为绝对路径字符串。

        Args:
            path: 输入路径。

        Returns:
            绝对路径字符串。
        """
        kaos_path = path if isinstance(path, KaosPath) else KaosPath(path)
        return str(kaos_path.canonical())