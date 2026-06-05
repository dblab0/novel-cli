"""SearchEntity 工具：小说实体搜索工具。

提供 SearchEntity 工具，用于在小说知识库中搜索实体（人物、法宝、门派等），
支持语义向量搜索和精确名称/别名搜索两种模式。
"""

from pathlib import Path
from typing import Literal, override

from novel_cli.config import Config

from kosong.tooling import ToolError, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.tools.novel._base import NovelToolBase
from novel_cli.tools.novel.errors import MissingParamError, SearchNovelError
from novel_cli.tools.novel.formatter import format_entity_result
from novel_cli.tools.utils import load_desc


class SearchEntityParams(BaseModel):
    """小说实体搜索参数。

    Attributes:
        query: 搜索文本。
        book: 书名过滤（精确匹配）。
        top_k: 返回结果数量。
        search_mode: 搜索模式：vector=语义向量搜索，name=精确名称/别名搜索。
    """

    query: str = Field(
        description=(
            "A single keyword or entity name to search for. "
            "Using multiple keywords in one call dilutes search accuracy. "
            "For multiple entities, make separate parallel calls."
        ),
    )
    book: str | None = Field(
        default=None,
        description="Book name filter (exact match). Set by the system when a book is selected.",
    )
    top_k: int = Field(
        default=5,
        description="Maximum number of results to return.",
    )
    search_mode: Literal["vector", "name"] = Field(
        default="vector",
        description=(
            "`vector`: semantic similarity search — use when you only know a description or characteristic. "
            "`name`: exact name/alias match — use when you know the entity name."
        ),
    )


class SearchEntity(NovelToolBase[SearchEntityParams]):
    """小说实体搜索工具。

    用于在小说知识库中搜索实体（人物、法宝、门派等），
    支持两种搜索模式：
    1. vector 模式：语义向量搜索，适用于按描述或特征搜索
    2. name 模式：精确名称/别名搜索，适用于已知实体名称的搜索

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "SearchEntity"
    description: str = load_desc(Path(__file__).parent / "entity.md", {})
    params: type[SearchEntityParams] = SearchEntityParams

    def __init__(self, config: Config) -> None:
        """初始化 SearchEntity 工具。

        Args:
            config: 应用配置对象，包含数据库连接信息。

        Raises:
            SkipThisTool: 如果数据库连接未配置（密码为空），则跳过此工具。
        """
        super().__init__(config)

    @override
    async def __call__(self, params: SearchEntityParams) -> ToolReturnValue:
        """执行实体搜索操作。

        在执行查询前确保数据库连接已建立，连接失败时返回 ToolError。

        Args:
            params: 搜索参数，包含查询文本和过滤条件。

        Returns:
            操作结果，搜索结果或错误信息。
        """
        try:
            await self._ensure_connected()
        except Exception as e:
            return ToolError(
                message=f"小说知识库连接失败: {e}",
                brief="数据库连接失败",
            )

        assert self._store is not None

        try:
            if not params.query:
                raise MissingParamError(
                    message='需要提供 query 参数。示例: query="韩立"',
                    brief="参数缺失: query",
                )

            if params.search_mode == "name":
                result = await self._store.search_entities_by_name(
                    name=params.query,
                    book=params.book,
                )
            else:
                result = await self._store.search_entities(
                    query=params.query,
                    book=params.book,
                    top_k=params.top_k,
                )
            return format_entity_result(result)
        except SearchNovelError as e:
            return e.to_tool_error()
        except Exception as e:
            return ToolError(
                message=f"SearchEntity 内部错误: {e}",
                brief="内部错误",
            )
