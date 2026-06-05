"""小说搜索工具错误层级定义。

定义小说搜索工具使用的各种错误类型，
用于处理参数缺失、实体不存在、数据库连接等问题。
"""

from __future__ import annotations

from kosong.tooling import ToolError


class SearchNovelError(Exception):
    """小说搜索工具的基础错误类。

    所有小说搜索工具相关错误的基类，提供统一的错误消息和简短描述。

    Attributes:
        message: 详细错误消息。
        brief: 简短错误描述。
    """

    def __init__(self, message: str, brief: str) -> None:
        """初始化小说搜索工具错误。

        Args:
            message: 详细错误消息。
            brief: 简短错误描述。
        """
        self.message = message
        self.brief = brief
        super().__init__(message)

    def to_tool_error(self) -> ToolError:
        """转换为工具错误对象。

        Returns:
            ToolError 对象，包含错误消息和简短描述。
        """
        return ToolError(message=self.message, brief=self.brief)


class MissingParamError(SearchNovelError):
    """必需参数缺失错误。

    当缺少必需的查询参数时抛出此错误。
    """


class EntityNotFoundError(SearchNovelError):
    """实体不存在错误。

    在知识库中找不到指定实体时抛出此错误。
    """


class InvalidEntityIdError(SearchNovelError):
    """实体 ID 格式无效错误。

    当实体 ID 格式不符合要求时抛出此错误。
    """


class DBConnectionError(SearchNovelError):
    """数据库连接失败错误。

    无法连接到数据库时抛出此错误。
    """


class BookNotFoundError(SearchNovelError):
    """书名不存在错误。

    在语料库中找不到指定书名时抛出此错误。
    """
