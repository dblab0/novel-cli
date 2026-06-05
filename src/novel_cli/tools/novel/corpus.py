"""SearchCorpus 工具：小说原文关键词搜索工具。

提供 SearchCorpus 工具，用于按关键词在小说原文中查找相关段落。
"""

from pathlib import Path
from typing import override

from novel_cli.config import Config

from kosong.tooling import ToolError, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.tools.novel._base import NovelToolBase
from novel_cli.tools.novel.errors import MissingParamError, SearchNovelError
from novel_cli.tools.novel.formatter import format_corpus_search_result
from novel_cli.tools.utils import load_desc


class SearchCorpusParams(BaseModel):
    """小说原文关键词搜索参数。

    Attributes:
        book: 书名过滤（精确匹配）。
        keyword: 搜索关键词。
        chapter_ids: 章节 ID 列表（限定搜索范围，推荐从 Graph 结果获取）。
        context_size: 关键词匹配周围的上下文句子数量。
    """

    book: str | None = Field(
        default=None,
        description="Book name filter (exact match). Set by the system when a book is selected.",
    )
    keyword: str = Field(
        description=(
            "Search keyword to find relevant passages in the novel text. "
            "Returns matching sentences with surrounding context. "
            "Use `context_size` to control how many adjacent sentences are included."
        ),
    )
    chapter_ids: list[int] | None = Field(
        default=None,
        description=(
            "List of chapter IDs to scope the keyword search. "
            "Recommended to obtain from SearchGraph results. "
            "Omit to search the entire book."
        ),
    )
    context_size: int = Field(
        default=1,
        description=(
            "Number of context sentences around each keyword match. "
            "Increase for richer context (e.g. 3-5), decrease for concise results."
        ),
    )


class SearchCorpus(NovelToolBase[SearchCorpusParams]):
    """小说原文关键词搜索工具。

    第一步主动搜索工具，按关键词在小说原文中查找相关段落。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "SearchCorpus"
    description: str = load_desc(Path(__file__).parent / "corpus.md", {})
    params: type[SearchCorpusParams] = SearchCorpusParams

    def __init__(self, config: Config) -> None:
        """初始化 SearchCorpus 工具。

        Args:
            config: 应用配置对象，包含数据库连接信息。

        Raises:
            SkipThisTool: 如果数据库连接未配置（密码为空），则跳过此工具。
        """
        super().__init__(config)

    @override
    async def __call__(self, params: SearchCorpusParams) -> ToolReturnValue:
        """执行关键词搜索操作。

        在执行查询前确保数据库连接已建立，连接失败时返回 ToolError。

        Args:
            params: 搜索参数，包含关键词和可选的章节范围限定。

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
            if not params.book:
                raise MissingParamError(
                    message='请先使用 /book 选择书籍',
                    brief="请使用 /book 选择书籍",
                )

            result = await self._store.query_corpus(
                book=params.book,
                keywords=[params.keyword],
                chapter_ids=params.chapter_ids,
                context_size=params.context_size,
            )
            return format_corpus_search_result(params.book, result)
        except SearchNovelError as e:
            return e.to_tool_error()
        except Exception as e:
            return ToolError(
                message=f"SearchCorpus 内部错误: {e}",
                brief="内部错误",
            )
