"""文件操作工具模块。

提供文件读取、写入、编辑、搜索等操作工具。
"""

from enum import StrEnum


class FileOpsWindow:
    """文件操作窗口类。

    用于维护文件操作的窗口状态。
    """

    pass


class FileActions(StrEnum):
    """文件操作类型枚举。

    Attributes:
        READ: 读取文件操作。
        EDIT: 编辑文件操作。
        EDIT_OUTSIDE: 编辑工作目录外文件操作。
    """

    READ = "read file"
    EDIT = "edit file"
    EDIT_OUTSIDE = "edit file outside of working directory"


from .glob import Glob  # noqa: E402
from .grep_local import Grep  # noqa: E402
from .read import ReadFile  # noqa: E402
from .read_media import ReadMediaFile  # noqa: E402
from .replace import StrReplaceFile  # noqa: E402
from .write import WriteFile  # noqa: E402

__all__ = (
    "ReadFile",
    "ReadMediaFile",
    "Glob",
    "Grep",
    "WriteFile",
    "StrReplaceFile",
)
