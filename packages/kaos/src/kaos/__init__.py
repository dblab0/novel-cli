"""KAOS (Kimi Agent Operating System) 核心模块。

本模块定义了 KAOS 的核心协议和接口，提供跨平台的文件系统抽象层。
KAOS 支持本地文件系统和远程 SSH 文件系统，为上层应用提供统一的操作接口。

主要组件：
    - AsyncReadable: 异步可读字节流协议
    - AsyncWritable: 异步可写字节流协议
    - KaosProcess: 进程接口协议
    - Kaos: 文件系统操作协议
    - StatResult: 文件状态结果数据类
"""

from __future__ import annotations

import contextvars
from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from asyncio import StreamReader, StreamWriter

    from asyncssh.stream import SSHReader, SSHWriter

    from kaos.path import KaosPath

    def type_check(
        stream_reader: StreamReader,
        stream_writer: StreamWriter,
        ssh_reader: SSHReader[bytes],
        ssh_writer: SSHWriter[bytes],
    ):
        _reader: AsyncReadable = stream_reader
        _reader = ssh_reader
        _writer: AsyncWritable = stream_writer
        _writer = ssh_writer


type StrOrKaosPath = str | KaosPath
"""字符串或 KaosPath 类型别名，用于接受路径参数。"""


@runtime_checkable
class AsyncReadable(Protocol):
    """描述可读异步字节流的协议。

    该协议定义了异步读取字节数据的标准接口，兼容 asyncio.StreamReader
    和 asyncssh.SSHReader。
    """

    def __aiter__(self) -> AsyncIterator[bytes]:
        """按数据块（通常是行）异步迭代产出。"""
        ...

    def at_eof(self) -> bool:
        """当流已到达 EOF 且缓冲区为空时返回 True。"""
        ...

    def feed_data(self, data: bytes) -> None:
        """向流中注入数据；主要用于测试或适配器。"""
        ...

    def feed_eof(self) -> None:
        """向流发送文件结束信号。"""
        ...

    async def read(self, n: int = -1) -> bytes:
        """读取最多 n 个字节；-1 表示读取直到 EOF。"""
        ...

    async def readline(self) -> bytes:
        """读取单行，以换行符或 EOF 结尾。"""
        ...

    async def readexactly(self, n: int) -> bytes:
        """精确读取 n 个字节，否则抛出 IncompleteReadError。"""
        ...

    async def readuntil(self, separator: bytes) -> bytes:
        """读取直到遇到分隔符，包含分隔符。"""
        ...


@runtime_checkable
class AsyncWritable(Protocol):
    """描述可写异步字节流的协议。

    该协议定义了异步写入字节数据的标准接口，兼容 asyncio.StreamWriter
    和 asyncssh.SSHWriter。
    """

    def can_write_eof(self) -> bool:
        """检查是否支持 write_eof() 方法。

        Returns:
            如果支持发送 EOF 则返回 True。
        """
        ...

    def close(self) -> None:
        """安排关闭底层传输。"""
        ...

    async def drain(self) -> None:
        """阻塞直到内部写缓冲区刷新完成。"""
        ...

    def is_closing(self) -> bool:
        """检查流是否已关闭或正在关闭。

        Returns:
            流已关闭或正在关闭时返回 True。
        """
        ...

    async def wait_closed(self) -> None:
        """等待关闭握手完成。"""
        ...

    def write(self, data: bytes) -> None:
        """向流写入原始字节。

        Args:
            data: 要写入的字节数据。
        """
        ...

    def writelines(self, data: Iterable[bytes], /) -> None:
        """向流写入可迭代的字节块。

        Args:
            data: 可迭代的字节块。
        """
        ...

    def write_eof(self) -> None:
        """如果支持，向底层传输发送 EOF。"""
        ...


@runtime_checkable
class KaosProcess(Protocol):
    """KAOS `exec` 实现暴露的进程接口。

    该协议定义了进程的标准接口，包括标准输入/输出/错误流、
    进程 ID、返回码以及进程控制方法。

    Attributes:
        stdin: 标准输入流，可写入。
        stdout: 标准输出流，可读取。
        stderr: 标准错误流，可读取。
    """

    stdin: AsyncWritable
    stdout: AsyncReadable
    stderr: AsyncReadable

    @property
    def pid(self) -> int:
        """获取进程 ID。

        Returns:
            进程 ID。
        """
        ...

    @property
    def returncode(self) -> int | None:
        """获取进程返回码。

        Returns:
            进程返回码，如果仍在运行则返回 None。
        """

    async def wait(self) -> int:
        """等待进程完成并返回退出码。

        Returns:
            进程退出码。
        """

    async def kill(self) -> None:
        """终止进程。"""
        ...


@runtime_checkable
class Kaos(Protocol):
    """Kimi Agent Operating System (KAOS) 接口。

    该协议定义了文件系统操作的标准接口，支持本地和远程文件系统。
    KAOS 提供跨平台的文件系统抽象，包括路径操作、文件读写、
    目录管理和进程执行等功能。

    Attributes:
        name: KAOS 实现的名称（如 "local" 或 "ssh"）。
    """

    name: str
    """KAOS 实现的名称。"""

    def pathclass(self) -> type[PurePath]:
        """获取 KaosPath 下使用的路径类。

        Returns:
            路径类（PurePosixPath 或 PureWindowsPath）。
        """
        ...

    def normpath(self, path: StrOrKaosPath) -> KaosPath:
        """规范化路径，消除双斜杠等。

        Args:
            path: 要规范化的路径。

        Returns:
            规范化后的 KaosPath。
        """

    def gethome(self) -> KaosPath:
        """获取主目录路径。

        Returns:
            主目录的 KaosPath。
        """

    def getcwd(self) -> KaosPath:
        """获取当前工作目录路径。

        Returns:
            当前工作目录的 KaosPath。
        """

    async def chdir(self, path: StrOrKaosPath) -> None:
        """更改当前工作目录。

        Args:
            path: 目标路径。
        """

    async def stat(self, path: StrOrKaosPath, *, follow_symlinks: bool = True) -> StatResult:
        """获取路径的 stat 结果。

        Args:
            path: 要查询的路径。
            follow_symlinks: 是否跟随符号链接。

        Returns:
            文件状态信息。
        """

    def iterdir(self, path: StrOrKaosPath) -> AsyncGenerator[KaosPath]:
        """遍历目录中的条目。

        Args:
            path: 目录路径。

        Returns:
            目录条目的异步生成器。
        """

    def glob(
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

    async def readbytes(self, path: StrOrKaosPath, n: int | None = None) -> bytes:
        """读取整个文件内容为字节。

        Args:
            path: 文件路径。
            n: 要读取的字节数，None 表示读取全部。

        Returns:
            文件内容字节。
        """

    async def readtext(
        self,
        path: StrOrKaosPath,
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

    def readlines(
        self,
        path: StrOrKaosPath,
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

    async def writebytes(self, path: StrOrKaosPath, data: bytes) -> int:
        """将字节数据写入文件。

        Args:
            path: 文件路径。
            data: 要写入的字节数据。

        Returns:
            写入的字节数。
        """

    async def writetext(
        self,
        path: StrOrKaosPath,
        data: str,
        *,
        mode: Literal["w", "a"] = "w",
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

    async def mkdir(
        self, path: StrOrKaosPath, parents: bool = False, exist_ok: bool = False
    ) -> None:
        """在给定路径创建目录。

        Args:
            path: 目录路径。
            parents: 是否创建父目录。
            exist_ok: 目录已存在时是否忽略错误。
        """

    async def exec(self, *args: str, env: Mapping[str, str] | None = None) -> KaosProcess:
        """执行带参数的命令并返回运行中的进程。

        Args:
            *args: 命令及其参数。
            env: 子进程的环境变量。如果为 None，则从父进程继承。

        Returns:
            运行中的进程实例。
        """
        ...


@dataclass
class StatResult:
    """KAOS stat 结果数据类。

    封装文件状态信息，与 os.stat_result 结构兼容。

    Attributes:
        st_mode: 文件模式和权限位。
        st_ino: 文件 inode 号。
        st_dev: 文件所在设备号。
        st_nlink: 文件的硬链接数。
        st_uid: 文件所有者的用户 ID。
        st_gid: 文件所有者的组 ID。
        st_size: 文件大小（字节）。
        st_atime: 最近访问时间（秒）。
        st_mtime: 最近修改时间（秒）。
        st_ctime: 最近状态更改时间（秒）。
    """

    st_mode: int
    st_ino: int
    st_dev: int
    st_nlink: int
    st_uid: int
    st_gid: int
    st_size: int
    st_atime: float
    st_mtime: float
    st_ctime: float


def get_current_kaos() -> Kaos:
    """获取当前 KAOS 实例。

    Returns:
        当前上下文中活跃的 KAOS 实例。
    """
    from kaos._current import current_kaos

    return current_kaos.get()


def set_current_kaos(kaos: Kaos) -> contextvars.Token[Kaos]:
    """设置当前 KAOS 实例。

    Args:
        kaos: 要设置为当前实例的 KAOS。

    Returns:
        用于后续重置的上下文变量令牌。
    """
    from kaos._current import current_kaos

    return current_kaos.set(kaos)


def reset_current_kaos(token: contextvars.Token[Kaos]) -> None:
    """重置当前 KAOS 实例。

    Args:
        token: 之前 set_current_kaos 返回的令牌。
    """
    from kaos._current import current_kaos

    current_kaos.reset(token)


def pathclass() -> type[PurePath]:
    """获取当前 KAOS 实例使用的路径类。

    Returns:
        路径类（PurePosixPath 或 PureWindowsPath）。
    """
    return get_current_kaos().pathclass()


def normpath(path: StrOrKaosPath) -> KaosPath:
    """规范化路径。

    Args:
        path: 要规范化的路径。

    Returns:
        规范化后的 KaosPath。
    """
    return get_current_kaos().normpath(path)


def gethome() -> KaosPath:
    """获取当前 KAOS 实例的主目录路径。

    Returns:
        主目录的 KaosPath。
    """
    return get_current_kaos().gethome()


def getcwd() -> KaosPath:
    """获取当前 KAOS 实例的工作目录路径。

    Returns:
        当前工作目录的 KaosPath。
    """
    return get_current_kaos().getcwd()


async def chdir(path: StrOrKaosPath) -> None:
    """更改当前 KAOS 实例的工作目录。

    Args:
        path: 目标路径。
    """
    await get_current_kaos().chdir(path)


async def stat(path: StrOrKaosPath, *, follow_symlinks: bool = True) -> StatResult:
    """获取路径的文件状态信息。

    Args:
        path: 要查询的路径。
        follow_symlinks: 是否跟随符号链接。

    Returns:
        文件状态信息。
    """
    return await get_current_kaos().stat(path, follow_symlinks=follow_symlinks)


def iterdir(path: StrOrKaosPath) -> AsyncGenerator[KaosPath]:
    """遍历目录中的条目。

    Args:
        path: 目录路径。

    Returns:
        目录条目的异步生成器。
    """
    return get_current_kaos().iterdir(path)


def glob(
    path: StrOrKaosPath, pattern: str, *, case_sensitive: bool = True
) -> AsyncGenerator[KaosPath]:
    """在给定路径下搜索匹配模式的文件/目录。

    Args:
        path: 搜索的基础路径。
        pattern: glob 模式。
        case_sensitive: 是否区分大小写。

    Returns:
        匹配路径的异步生成器。
    """
    return get_current_kaos().glob(path, pattern, case_sensitive=case_sensitive)


async def readbytes(path: StrOrKaosPath, n: int | None = None) -> bytes:
    """读取整个文件内容为字节。

    Args:
        path: 文件路径。
        n: 要读取的字节数，None 表示读取全部。

    Returns:
        文件内容字节。
    """
    return await get_current_kaos().readbytes(path, n=n)


async def readtext(
    path: StrOrKaosPath,
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
    return await get_current_kaos().readtext(path, encoding=encoding, errors=errors)


def readlines(
    path: StrOrKaosPath,
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
    return get_current_kaos().readlines(path, encoding=encoding, errors=errors)


async def writebytes(path: StrOrKaosPath, data: bytes) -> int:
    """将字节数据写入文件。

    Args:
        path: 文件路径。
        data: 要写入的字节数据。

    Returns:
        写入的字节数。
    """
    return await get_current_kaos().writebytes(path, data)


async def writetext(
    path: StrOrKaosPath,
    data: str,
    *,
    mode: Literal["w", "a"] = "w",
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
    return await get_current_kaos().writetext(
        path, data, mode=mode, encoding=encoding, errors=errors
    )


async def mkdir(path: StrOrKaosPath, parents: bool = False, exist_ok: bool = False) -> None:
    """在给定路径创建目录。

    Args:
        path: 目录路径。
        parents: 是否创建父目录。
        exist_ok: 目录已存在时是否忽略错误。
    """
    return await get_current_kaos().mkdir(path, parents=parents, exist_ok=exist_ok)


async def exec(*args: str, env: Mapping[str, str] | None = None) -> KaosProcess:
    """执行带参数的命令并返回运行中的进程。

    Args:
        *args: 命令及其参数。
        env: 子进程的环境变量。

    Returns:
        运行中的进程实例。
    """
    return await get_current_kaos().exec(*args, env=env)
