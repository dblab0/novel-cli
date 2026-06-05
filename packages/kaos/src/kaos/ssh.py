"""SSH 远程文件系统 KAOS 实现模块。

本模块提供基于 SSH 和 SFTP 的远程 KAOS 实现，
通过 asyncssh 库实现远程文件系统操作和进程执行。
"""

from __future__ import annotations

import posixpath
import shlex
import stat
from collections.abc import AsyncGenerator, Mapping
from pathlib import PurePath, PurePosixPath
from typing import TYPE_CHECKING, Literal

import asyncssh
from asyncssh.constants import (
    FILEXFER_TYPE_BLOCK_DEVICE,
    FILEXFER_TYPE_CHAR_DEVICE,
    FILEXFER_TYPE_DIRECTORY,
    FILEXFER_TYPE_FIFO,
    FILEXFER_TYPE_REGULAR,
    FILEXFER_TYPE_SOCKET,
    FILEXFER_TYPE_SYMLINK,
)

from kaos import AsyncReadable, AsyncWritable, Kaos, KaosProcess, StatResult, StrOrKaosPath
from kaos.path import KaosPath

if TYPE_CHECKING:

    def type_check(ssh: SSHKaos) -> None:
        _: Kaos = ssh


_FILEXFER_TYPE_TO_MODE = {
    FILEXFER_TYPE_REGULAR: stat.S_IFREG,
    FILEXFER_TYPE_DIRECTORY: stat.S_IFDIR,
    FILEXFER_TYPE_SYMLINK: stat.S_IFLNK,
    FILEXFER_TYPE_SOCKET: stat.S_IFSOCK,
    FILEXFER_TYPE_CHAR_DEVICE: stat.S_IFCHR,
    FILEXFER_TYPE_BLOCK_DEVICE: stat.S_IFBLK,
    FILEXFER_TYPE_FIFO: stat.S_IFIFO,
}


def _build_st_mode(attrs: asyncssh.SFTPAttrs) -> int:
    """将 SFTP 权限和类型信息合并为 st_mode。

    Args:
        attrs: SFTP 文件属性对象。

    Returns:
        合并后的 st_mode 值。
    """
    perm_mode = attrs.permissions or 0
    type_mode = _FILEXFER_TYPE_TO_MODE.get(attrs.type, 0)

    if perm_mode:
        if type_mode and stat.S_IFMT(perm_mode) == 0:
            perm_mode |= type_mode
        return perm_mode

    return type_mode


def _sec_with_nanos(sec: int, ns: int | None) -> float:
    """将秒和纳秒转换为浮点数时间。

    Args:
        sec: 秒数。
        ns: 纳秒数，可为 None。

    Returns:
        转换后的浮点数时间（秒）。
    """
    if ns is None:
        return float(sec)
    return float(sec) + (ns / 1_000_000_000.0)


class SSHKaos:
    """通过 SSH 和 SFTP 与远程机器交互的 KAOS 实现。

    该类实现了 Kaos 协议，提供远程文件系统的异步操作接口，
    包括文件读写、目录管理、路径操作和远程进程执行。

    Attributes:
        name: 实现名称，固定为 "ssh"。
        host: 远程主机地址。
    """

    name: str = "ssh"

    class Process:
        """asyncssh.SSHClientProcess 的 KAOS 进程包装器。

        该类封装 asyncssh 进程对象，提供标准的 KAOS 进程接口。

        Attributes:
            stdin: 标准输入流。
            stdout: 标准输出流。
            stderr: 标准错误流。
        """

        def __init__(self, process: asyncssh.SSHClientProcess[bytes]) -> None:
            """初始化进程包装器。

            Args:
                process: asyncssh SSH 客户端进程对象。
            """
            self._process = process
            self.stdin: AsyncWritable = process.stdin
            self.stdout: AsyncReadable = process.stdout
            self.stderr: AsyncReadable = process.stderr

        @property
        def pid(self) -> int:
            """获取进程 ID。

            Returns:
                进程 ID（SSH 进程不支持，返回 -1）。
            """
            # FIXME: SSHClientProcess 没有 pid 属性。
            return -1

        @property
        def returncode(self) -> int | None:
            """获取进程返回码。

            Returns:
                进程返回码，如果仍在运行则返回 None。
            """
            return self._process.returncode

        async def wait(self) -> int:
            """等待进程完成。

            使用 wait_closed() 以便 stdout/stderr 在 wait 后仍可读取，
            与 LocalKaos 行为一致。

            Returns:
                进程退出码。
            """
            # asyncssh.SSHClientProcess.wait() 通过 communicate() 清空 stdout/stderr
            # 这会清除内部接收缓冲区。使用 wait_closed() 以便
            # stdout/stderr 在 wait 后仍可读取，与 LocalKaos 行为一致。
            await self._process.wait_closed()
            return 1 if self._process.returncode is None else self._process.returncode

        async def kill(self) -> None:
            """终止进程。"""
            self._process.kill()

    @classmethod
    async def create(
        cls,
        host: str,
        *,
        port: int = 22,
        username: str | None = None,
        password: str | None = None,
        key_paths: list[str] | None = None,
        key_contents: list[str] | None = None,
        cwd: str | None = None,
        **extra_options: object,
    ):
        """创建 SSH KAOS 实例并建立连接。

        Args:
            host: 远程主机地址。
            port: SSH 端口，默认为 22。
            username: 用户名。
            password: 密码。
            key_paths: SSH 密钥文件路径列表。
            key_contents: SSH 密钥内容字符串列表。
            cwd: 初始工作目录。
            **extra_options: 其他 asyncssh.connect 选项。

        Returns:
            已连接的 SSHKaos 实例。
        """
        options = {
            "host": host,
            "port": port,
            **extra_options,
        }
        if username:
            options["username"] = username
        if password:
            options["password"] = password
        client_keys: list[str | asyncssh.SSHKey] = []
        if key_contents:
            client_keys.extend([asyncssh.import_private_key(key) for key in key_contents])
        if key_paths:
            client_keys.extend(key_paths)
        if client_keys:
            options["client_keys"] = client_keys
        # 确保 encoding 为 None 以读写字节
        options["encoding"] = None
        # known_hosts 设为 None 以避免"主机密钥不受信任"错误
        options["known_hosts"] = None
        # 连接到 SSH
        connection = await asyncssh.connect(**options)
        sftp = await connection.start_sftp_client()
        home_dir = await sftp.realpath(".")
        if cwd is not None:
            await sftp.chdir(cwd)
            cwd = await sftp.realpath(".")
        else:
            cwd = home_dir
        return cls(connection=connection, sftp=sftp, home=home_dir, cwd=cwd, host=host)

    def __init__(
        self,
        *,
        connection: asyncssh.SSHClientConnection,
        sftp: asyncssh.SFTPClient,
        home: str,
        cwd: str,
        host: str,
    ) -> None:
        """初始化 SSH KAOS 实例。

        Args:
            connection: SSH 连接对象。
            sftp: SFTP 客户端对象。
            home: 远程主目录路径。
            cwd: 当前工作目录路径。
            host: 远程主机地址。
        """
        self._connection = connection
        self._sftp = sftp
        self._home_dir = home
        self._cwd = cwd
        self._host = host

    @property
    def host(self) -> str:
        """获取远程主机地址。

        Returns:
            远程主机地址。
        """
        return self._host

    def pathclass(self) -> type[PurePath]:
        """获取路径类。

        Returns:
            PurePosixPath（SSH 连接使用 POSIX 路径）。
        """
        return PurePosixPath

    def normpath(self, path: StrOrKaosPath) -> KaosPath:
        """规范化路径。

        Args:
            path: 要规范化的路径。

        Returns:
            规范化后的 KaosPath。
        """
        return KaosPath(posixpath.normpath(str(path)))

    def gethome(self) -> KaosPath:
        """获取远程主目录路径。

        Returns:
            远程主目录的 KaosPath。
        """
        return KaosPath(self._home_dir)

    def getcwd(self) -> KaosPath:
        """获取当前工作目录路径。

        Returns:
            当前工作目录的 KaosPath。
        """
        return KaosPath(self._cwd)

    async def chdir(self, path: StrOrKaosPath) -> None:
        """更改当前工作目录。

        Args:
            path: 目标路径。
        """
        await self._sftp.chdir(str(path))
        self._cwd = await self._sftp.realpath(".")

    async def stat(
        self,
        path: StrOrKaosPath,
        *,
        follow_symlinks: bool = True,
    ) -> StatResult:
        """获取路径的文件状态信息。

        Args:
            path: 要查询的路径。
            follow_symlinks: 是否跟随符号链接。

        Returns:
            文件状态信息。

        Raises:
            OSError: 无法获取文件状态时抛出。
        """
        try:
            st = await self._sftp.stat(str(path), follow_symlinks=follow_symlinks)
        except asyncssh.SFTPError as e:
            raise OSError from e

        return StatResult(
            st_mode=_build_st_mode(st),
            st_uid=st.uid or 0,
            st_gid=st.gid or 0,
            st_size=st.size or 0,
            st_atime=_sec_with_nanos(st.atime or 0, st.atime_ns),
            st_mtime=_sec_with_nanos(st.mtime or 0, st.mtime_ns),
            st_ctime=_sec_with_nanos(st.ctime or 0, st.ctime_ns),
            st_ino=0,  # SFTP 不支持 ino
            st_dev=0,  # SFTP 不支持 dev
            st_nlink=st.nlink or 0,
        )

    async def iterdir(self, path: StrOrKaosPath) -> AsyncGenerator[KaosPath]:
        """遍历目录中的条目。

        Args:
            path: 目录路径。

        Returns:
            目录条目的异步生成器。
        """
        kaos_path = KaosPath(path) if isinstance(path, str) else path
        for entry in await self._sftp.listdir(str(path)):
            # 注意：SFTP listdir 会返回 . 和 ..
            if entry in {".", ".."}:
                continue
            yield kaos_path / entry

    async def glob(
        self,
        path: StrOrKaosPath,
        pattern: str,
        *,
        case_sensitive: bool = True,
    ) -> AsyncGenerator[KaosPath]:
        """在给定路径下搜索匹配模式的文件/目录。

        Args:
            path: 搜索的基础路径。
            pattern: glob 模式。
            case_sensitive: 是否区分大小写。

        Returns:
            匹配路径的异步生成器。

        Raises:
            ValueError: 不支持不区分大小写的 glob。
        """
        if not case_sensitive:
            raise ValueError("当前环境不支持不区分大小写的 glob")
        real_path = await self._sftp.realpath(str(path))
        for entry in await self._sftp.glob(f"{real_path}/{pattern}"):
            yield KaosPath(await self._sftp.realpath(str(entry)))

    async def readbytes(self, path: StrOrKaosPath, n: int | None = None) -> bytes:
        """读取整个文件内容为字节。

        Args:
            path: 文件路径。
            n: 要读取的字节数，None 表示读取全部。

        Returns:
            文件内容字节。
        """
        async with self._sftp.open(str(path), "rb") as f:
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
        async with self._sftp.open(str(path), "r", encoding=encoding, errors=errors) as f:
            return await f.read()

    async def readlines(
        self,
        path: str | KaosPath,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> AsyncGenerator[str]:
        """遍历文件的行。

        注意：SFTPClientFile 不支持 readlines，此方法通过 readtext 实现。

        Args:
            path: 文件路径。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            文件行的异步生成器。
        """
        # 注意：SFTPClientFile 不支持 readlines
        text = await self.readtext(path, encoding=encoding, errors=errors)
        for line in text.splitlines():
            yield line

    async def writebytes(self, path: StrOrKaosPath, data: bytes) -> int:
        """将字节数据写入文件。

        Args:
            path: 文件路径。
            data: 要写入的字节数据。

        Returns:
            写入的字节数。
        """
        async with self._sftp.open(str(path), "wb") as f:
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
        async with self._sftp.open(str(path), mode, encoding=encoding, errors=errors) as f:
            return await f.write(data)

    async def mkdir(
        self,
        path: StrOrKaosPath,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        """在给定路径创建目录。

        Args:
            path: 目录路径。
            parents: 是否创建父目录。
            exist_ok: 目录已存在时是否忽略错误。

        Raises:
            FileExistsError: 目录已存在且 exist_ok 为 False。
        """
        if parents:
            await self._sftp.makedirs(str(path), exist_ok=exist_ok)
        else:
            existed = await self._sftp.exists(str(path))
            if existed and not exist_ok:
                raise FileExistsError(f"{path} 已存在")
            await self._sftp.mkdir(str(path))

    async def exec(self, *args: str, env: Mapping[str, str] | None = None) -> KaosProcess:
        """执行带参数的命令并返回运行中的进程。

        注意：为使 exec 表现得像其他 KAOS 后端，会显式 cd 到跟踪的 cwd 后运行命令。
        如果 cwd 不存在，命令会失败。

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
        command = " ".join(shlex.quote(arg) for arg in args)
        # 注意：
        # - SFTP 有自己的工作目录概念；它不影响 SSH exec。
        # - 为了使 exec 表现得像其他 KAOS 后端，我们显式地 cd 到我们跟踪的
        #   cwd 然后运行命令。
        #
        # 这是有意严格设计的：如果 cwd 不存在，命令会失败。
        if self._cwd:
            command = f"cd {shlex.quote(self._cwd)} && {command}"
        process = await self._connection.create_process(command, encoding=None, env=env)
        return self.Process(process)

    async def unsafe_close(self) -> None:
        """关闭 SSH 连接。

        调用此方法后 SSHKaos 将不可用。
        """
        if self._sftp:
            self._sftp.exit()
        if self._connection:
            self._connection.close()
