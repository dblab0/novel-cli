"""KAOS 文件系统路径抽象模块。

本模块提供 KaosPath 类，作为跨平台文件系统路径的统一抽象。
KaosPath 支持本地和远程（SSH）文件系统的路径操作。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path, PurePath
from stat import S_ISDIR, S_ISREG
from typing import Any, Literal

import kaos


class KaosPath:
    """KAOS 文件系统的路径抽象。

    该类提供跨平台路径操作接口，支持本地和远程文件系统。
    路径操作委托给当前 KAOS 实例的路径类实现。

    Attributes:
        name: 路径的最后一个组成部分。
        parent: 路径的父目录。
    """

    def __init__(self, *args: str) -> None:
        """初始化 KaosPath。

        Args:
            *args: 路径组件字符串。
        """
        self._path: PurePath = kaos.pathclass()(*args)

    @classmethod
    def unsafe_from_local_path(cls, path: Path) -> KaosPath:
        """从本地 Path 创建 KaosPath。

        仅在确定使用 LocalKaos 时使用此方法。

        Args:
            path: 本地 Path 对象。

        Returns:
            对应的 KaosPath。

        Warning:
            此方法仅适用于本地 KAOS 实例，在 SSH KAOS 上使用可能导致错误。
        """
        return cls(str(path))

    def unsafe_to_local_path(self) -> Path:
        """将 KaosPath 转换为本地 Path。

        仅在确定使用 LocalKaos 时使用此方法。

        Returns:
            对应的本地 Path 对象。

        Warning:
            此方法仅适用于本地 KAOS 实例，在 SSH KAOS 上使用可能导致错误。
        """
        return Path(str(self._path))

    def __lt__(self, other: KaosPath) -> bool:
        """小于比较运算符。

        Args:
            other: 比较的另一个 KaosPath。

        Returns:
            如果此路径小于 other 则返回 True。
        """
        return self._path.__lt__(other._path)

    def __le__(self, other: KaosPath) -> bool:
        """小于等于比较运算符。

        Args:
            other: 比较的另一个 KaosPath。

        Returns:
            如果此路径小于等于 other 则返回 True。
        """
        return self._path.__le__(other._path)

    def __gt__(self, other: KaosPath) -> bool:
        """大于比较运算符。

        Args:
            other: 比较的另一个 KaosPath。

        Returns:
            如果此路径大于 other 则返回 True。
        """
        return self._path.__gt__(other._path)

    def __ge__(self, other: KaosPath) -> bool:
        """大于等于比较运算符。

        Args:
            other: 比较的另一个 KaosPath。

        Returns:
            如果此路径大于等于 other 则返回 True。
        """
        return self._path.__ge__(other._path)

    def __eq__(self, other: Any) -> bool:
        """等于比较运算符。

        Args:
            other: 比较的另一个对象。

        Returns:
            如果路径相等则返回 True，类型不匹配返回 NotImplemented。
        """
        if not isinstance(other, KaosPath):
            return NotImplemented
        return self._path.__eq__(other._path)

    def __repr__(self) -> str:
        """返回路径的字符串表示。

        Returns:
            KaosPath 的字符串表示形式。
        """
        return f"KaosPath({repr(str(self._path))})"

    def __str__(self) -> str:
        """返回路径的字符串形式。

        Returns:
            路径字符串。
        """
        return str(self._path)

    @property
    def name(self) -> str:
        """返回路径的最后一个组成部分。

        Returns:
            文件或目录名。
        """
        return self._path.name

    @property
    def parent(self) -> KaosPath:
        """返回路径的父目录。

        Returns:
            父目录的 KaosPath。
        """
        return KaosPath(str(self._path.parent))

    def is_absolute(self) -> bool:
        """检查是否为绝对路径。

        Returns:
            如果是绝对路径则返回 True。
        """
        return self._path.is_absolute()

    def joinpath(self, *other: str) -> KaosPath:
        """将此路径与其他路径组件连接。

        Args:
            *other: 要连接的路径组件。

        Returns:
            连接后的 KaosPath。
        """
        return KaosPath(str(self._path.joinpath(*other)))

    def __truediv__(self, other: str | KaosPath) -> KaosPath:
        """使用 / 运算符连接路径。

        Args:
            other: 要连接的路径组件。

        Returns:
            连接后的 KaosPath。
        """
        p = other._path if isinstance(other, KaosPath) else other
        ret = KaosPath()
        ret._path = self._path.__truediv__(p)
        return ret

    def canonical(self) -> KaosPath:
        """将路径转换为绝对路径并规范化。

        解析路径中的所有 `.` 和 `..`，但不解析符号链接。
        与 pathlib.Path.resolve 不同，此方法不访问文件系统。

        Returns:
            规范化的绝对路径 KaosPath。
        """
        abs_path = self if self.is_absolute() else kaos.getcwd().joinpath(str(self._path))
        # 规范化路径（处理 . 和 ..）但保留格式
        normalized = kaos.normpath(abs_path)
        # normpath 可能会去掉尾部斜杠，但我们遵循 pathlib 的行为
        return normalized

    def relative_to(self, other: KaosPath) -> KaosPath:
        """返回从 other 到此路径的相对路径。

        Args:
            other: 基准路径。

        Returns:
            相对路径的 KaosPath。
        """
        relative_path = self._path.relative_to(other._path)
        return KaosPath(str(relative_path))

    @classmethod
    def home(cls) -> KaosPath:
        """返回主目录作为 KaosPath。

        Returns:
            主目录的 KaosPath。
        """
        return kaos.gethome()

    @classmethod
    def cwd(cls) -> KaosPath:
        """返回当前工作目录作为 KaosPath。

        Returns:
            当前工作目录的 KaosPath。
        """
        return kaos.getcwd()

    def expanduser(self) -> KaosPath:
        """将 ~ 展开为后端主目录。

        Returns:
            展开后的 KaosPath。
        """
        parts = self._path.parts
        if not parts or parts[0] != "~":
            return self

        home = KaosPath.home()
        if len(parts) == 1:
            return home
        return home.joinpath(*parts[1:])

    async def stat(self, follow_symlinks: bool = True) -> kaos.StatResult:
        """返回路径的文件状态信息。

        Args:
            follow_symlinks: 是否跟随符号链接。

        Returns:
            文件状态信息。
        """
        return await kaos.stat(self, follow_symlinks=follow_symlinks)

    async def exists(self, *, follow_symlinks: bool = True) -> bool:
        """检查路径是否存在。

        Args:
            follow_symlinks: 是否跟随符号链接。

        Returns:
            如果路径指向现有的文件系统条目则返回 True。
        """
        try:
            await self.stat(follow_symlinks=follow_symlinks)
            return True
        except OSError:
            return False

    async def is_file(self, *, follow_symlinks: bool = True) -> bool:
        """检查路径是否为常规文件。

        Args:
            follow_symlinks: 是否跟随符号链接。

        Returns:
            如果路径指向常规文件则返回 True。
        """
        try:
            st = await self.stat(follow_symlinks=follow_symlinks)
            return S_ISREG(st.st_mode)
        except OSError:
            return False

    async def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        """检查路径是否为目录。

        Args:
            follow_symlinks: 是否跟随符号链接。

        Returns:
            如果路径指向目录则返回 True。
        """
        try:
            st = await self.stat(follow_symlinks=follow_symlinks)
            return S_ISDIR(st.st_mode)
        except OSError:
            return False

    def iterdir(self) -> AsyncGenerator[KaosPath]:
        """返回目录的直接子项。

        Returns:
            目录子项的异步生成器。
        """
        return kaos.iterdir(self)

    def glob(self, pattern: str, *, case_sensitive: bool = True) -> AsyncGenerator[KaosPath]:
        """返回此目录下匹配模式的所有路径。

        Args:
            pattern: glob 模式。
            case_sensitive: 是否区分大小写。

        Returns:
            匹配路径的异步生成器。
        """
        return kaos.glob(self, pattern, case_sensitive=case_sensitive)

    async def read_bytes(self, n: int | None = None) -> bytes:
        """读取整个文件内容为字节。

        Args:
            n: 要读取的字节数，None 表示读取全部。

        Returns:
            文件内容字节。
        """
        return await kaos.readbytes(self, n=n)

    async def read_text(
        self,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> str:
        """读取整个文件内容为文本。

        Args:
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            文件内容文本。
        """
        return await kaos.readtext(self, encoding=encoding, errors=errors)

    def read_lines(
        self,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> AsyncGenerator[str]:
        """遍历文件的行。

        Args:
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            文件行的异步生成器。
        """
        return kaos.readlines(self, encoding=encoding, errors=errors)

    async def write_bytes(self, data: bytes) -> int:
        """将字节数据写入文件。

        Args:
            data: 要写入的字节数据。

        Returns:
            写入的字节数。
        """
        return await kaos.writebytes(self, data)

    async def write_text(
        self,
        data: str,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> int:
        """将文本数据写入文件。

        Args:
            data: 要写入的文本数据。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            写入的字符数。
        """
        return await kaos.writetext(
            self,
            data,
            mode="w",
            encoding=encoding,
            errors=errors,
        )

    async def append_text(
        self,
        data: str,
        *,
        encoding: str = "utf-8",
        errors: Literal["strict", "ignore", "replace"] = "strict",
    ) -> int:
        """将文本数据追加到文件。

        Args:
            data: 要追加的文本数据。
            encoding: 文本编码。
            errors: 编码错误处理方式。

        Returns:
            写入的字符数。
        """
        return await kaos.writetext(
            self,
            data,
            mode="a",
            encoding=encoding,
            errors=errors,
        )

    async def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        """在此路径创建目录。

        Args:
            parents: 是否创建父目录。
            exist_ok: 目录已存在时是否忽略错误。
        """
        return await kaos.mkdir(self, parents=parents, exist_ok=exist_ok)
