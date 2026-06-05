"""本地文件系统 KAOS 实现模块。

本模块提供基于本地文件系统的 KAOS 实现，通过 asyncio 和 aiofiles
实现异步文件操作和进程管理。
"""

from __future__ import annotations

import asyncio
import os
from asyncio.subprocess import Process as AsyncioProcess
from collections.abc import AsyncGenerator
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Literal

if os.name == "nt":
    import ntpath as pathmodule
    from pathlib import PureWindowsPath as PurePathClass
else:
    import posixpath as pathmodule
    from pathlib import PurePosixPath as PurePathClass

from collections.abc import Mapping

import aiofiles
import aiofiles.os

from kaos import AsyncReadable, AsyncWritable, Kaos, KaosProcess, StatResult, StrOrKaosPath
from kaos.path import KaosPath

if TYPE_CHECKING:

    def type_check(local: LocalKaos) -> None:
        _: Kaos = local


class LocalKaos:
    """直接与本地文件系统交互的 KAOS 实现。

    该类实现了 Kaos 协议，提供本地文件系统的异步操作接口，
    包括文件读写、目录管理、路径操作和进程执行。

    Attributes:
        name: 实现名称，固定为 "local"。
    """

    name: str = "local"

    class Process:
        """asyncio.subprocess.Process 的本地 KAOS 进程包装器。

        该类封装 asyncio 进程对象，提供标准的 KAOS 进程接口。

        Attributes:
            stdin: 标准输入流。
            stdout: 标准输出流。
            stderr: 标准错误流。
        """

        def __init__(self, process: AsyncioProcess) -> None:
            """初始化进程包装器。

            Args:
                process: asyncio 进程对象。

            Raises:
                ValueError: 进程未使用 stdin/stdout/stderr 管道创建。
            """
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise ValueError("进程必须使用 stdin/stdout/stderr 管道创建。")

            self._process = process
            self.stdin: AsyncWritable = process.stdin
            self.stdout: AsyncReadable = process.stdout
            self.stderr: AsyncReadable = process.stderr

        @property
        def pid(self) -> int:
            """获取进程 ID。

            Returns:
                进程 ID。
            """
            return self._process.pid

        @property
        def returncode(self) -> int | None:
            """获取进程返回码。

            Returns:
                进程返回码，如果仍在运行则返回 None。
            """
            return self._process.returncode

        async def wait(self) -> int:
            """等待进程完成。

            Returns:
                进程退出码。
            """
            return await self._process.wait()

        async def kill(self) -> None:
            """终止进程。"""
            self._process.kill()

    def pathclass(self) -> type[PurePath]:
        """获取路径类。

        Returns:
            根据操作系统返回 PureWindowsPath 或 PurePosixPath。
        """
        return PurePathClass

    def normpath(self, path: StrOrKaosPath) -> KaosPath:
        """规范化路径。

        Args:
            path: 要规范化的路径。

        Returns:
            规范化后的 KaosPath。
        """
        return KaosPath(pathmodule.normpath(str(path)))

    def gethome(self) -> KaosPath:
        """获取主目录路径。

        Returns:
            主目录的 KaosPath。
        """
        return KaosPath.unsafe_from_local_path(Path.home())

    def getcwd(self) -> KaosPath:
        """获取当前工作目录路径。

        Returns:
            当前工作目录的 KaosPath。
        """
        return KaosPath.unsafe_from_local_path(Path.cwd())

    async def chdir(self, path: StrOrKaosPath) -> None:
        """更改当前工作目录。

        Args:
            path: 目标路径。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        os.chdir(local_path)

    async def stat(self, path: StrOrKaosPath, *, follow_symlinks: bool = True) -> StatResult:
        """获取路径的文件状态信息。

        Args:
            path: 要查询的路径。
            follow_symlinks: 是否跟随符号链接。

        Returns:
            文件状态信息。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        st = await aiofiles.os.stat(local_path, follow_symlinks=follow_symlinks)
        return StatResult(
            st_mode=st.st_mode,
            st_ino=st.st_ino,
            st_dev=st.st_dev,
            st_nlink=st.st_nlink,
            st_uid=st.st_uid,
            st_gid=st.st_gid,
            st_size=st.st_size,
            st_atime=st.st_atime,
            st_mtime=st.st_mtime,
            st_ctime=st.st_ctime if os.name != "nt" else st.st_birthtime,
        )

    async def iterdir(self, path: StrOrKaosPath) -> AsyncGenerator[KaosPath]:
        """遍历目录中的条目。

        Args:
            path: 目录路径。

        Returns:
            目录条目的异步生成器。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        for entry in await aiofiles.os.listdir(local_path):
            yield KaosPath.unsafe_from_local_path(local_path / entry)

    async def glob(
        self, path: StrOrKaosPath, pattern: str, *, case_sensitive: bool = True
    ) -> AsyncGenerator[KaosPath]:
        """在给定路径下搜索匹配模式的文件/目录。

        Args:
            path: 搜索的基础路径。
            pattern: glob 模式。
            case_sensitive: 是否区分大小写。

        Returns:
            匹配路径的异步生成器。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        entries = await asyncio.to_thread(
            lambda: list(local_path.glob(pattern, case_sensitive=case_sensitive))
        )
        for entry in entries:
            yield KaosPath.unsafe_from_local_path(entry)

    async def readbytes(self, path: StrOrKaosPath, n: int | None = None) -> bytes:
        """读取整个文件内容为字节。

        Args:
            path: 文件路径。
            n: 要读取的字节数，None 表示读取全部。

        Returns:
            文件内容字节。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        async with aiofiles.open(local_path, mode="rb") as f:
            return await f.read() if n is None else await f.read(n)

    async def readtext(
        self,
        path: str | KaosPath,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> str:
        """读取整个文件内容为文本。

        Args:
            path: 文件路径。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            文件内容文本。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        async with aiofiles.open(local_path, encoding=encoding, errors=errors) as f:
            return await f.read()

    async def readlines(
        self,
        path: str | KaosPath,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> AsyncGenerator[str]:
        """遍历文件的行。

        Args:
            path: 文件路径。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            文件行的异步生成器。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        async with aiofiles.open(local_path, encoding=encoding, errors=errors) as f:
            async for line in f:
                yield line

    async def writebytes(self, path: StrOrKaosPath, data: bytes) -> int:
        """将字节数据写入文件。

        Args:
            path: 文件路径。
            data: 要写入的字节数据。

        Returns:
            写入的字节数。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        async with aiofiles.open(local_path, mode="wb") as f:
            return await f.write(data)

    async def writetext(
        self,
        path: str | KaosPath,
        data: str,
        *,
        mode: Literal["w"] | Literal["a"] = "w",
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> int:
        """将文本数据写入文件。

        Args:
            path: 文件路径。
            data: 要写入的文本数据。
            mode: 写入模式，"w" 为覆盖，"a" 为追加。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            写入的字符数。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        async with aiofiles.open(
            local_path, mode=mode, encoding=encoding, errors=errors, newline=""
        ) as f:
            return await f.write(data)

    async def mkdir(
        self, path: StrOrKaosPath, parents: bool = False, exist_ok: bool = False
    ) -> None:
        """在给定路径创建目录。

        Args:
            path: 目录路径。
            parents: 是否创建父目录。
            exist_ok: 目录已存在时是否忽略错误。
        """
        local_path = path.unsafe_to_local_path() if isinstance(path, KaosPath) else Path(path)
        await asyncio.to_thread(local_path.mkdir, parents=parents, exist_ok=exist_ok)

    async def exec(self, *args: str, env: Mapping[str, str] | None = None) -> KaosProcess:
        """执行带参数的命令并返回运行中的进程。

        Args:
            *args: 命令及其参数。
            env: 子进程的环境变量。

        Returns:
            运行中的进程实例。

        Raises:
            ValueError: 未提供要执行的命令。
        """
        if not args:
            raise ValueError("至少需要一个参数（要执行的程序）。")

        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        return self.Process(process)


local_kaos = LocalKaos()
"""默认的本地 KAOS 实例。"""
