"""ReadChapter 工具：小说章节读取工具。

提供 ReadChapter 工具，用于读取小说的完整章节或指定句子范围。
作为 SearchCorpus 空结果时的兜底/补全工具。
"""

from pathlib import Path
from typing import override

from novel_cli.config import Config

from kosong.tooling import ToolError, ToolReturnValue
from pydantic import BaseModel, Field, model_validator

from novel_cli.tools.novel._base import NovelToolBase
from novel_cli.tools.novel.errors import MissingParamError, SearchNovelError
from novel_cli.tools.novel.formatter import format_chapter_read_result
from novel_cli.tools.utils import load_desc


class ReadChapterParams(BaseModel):
    """小说章节读取参数。

    Attributes:
        book: 书名过滤（精确匹配）。
        chapter_id: 章节 ID。
        start: 起始句子索引（含）。
        end: 结束句子索引（不含）。
    """

    book: str | None = Field(
        default=None,
        description="Book name filter (exact match). Set by the system when a book is selected.",
    )
    chapter_id: int = Field(
        description="Chapter ID to read. Returns the full chapter content.",
    )
    start: int | None = Field(
        default=None,
        description="Start sentence index (inclusive). Omit to read from the beginning.",
    )
    end: int | None = Field(
        default=None,
        description="End sentence index (exclusive). Omit to read to the end of chapter.",
    )

    @model_validator(mode="after")
    def _validate_range(self) -> "ReadChapterParams":
        """start 和 end 必须同时传入或同时不传。"""
        if (self.start is None) != (self.end is None):
            raise ValueError("start 和 end 必须同时传入，或同时不传（读整章）")
        if self.start is not None and self.start >= self.end:  # type: ignore[operator]
            raise ValueError("start 必须小于 end")
        return self


class ReadChapter(NovelToolBase[ReadChapterParams]):
    """小说章节读取工具。

    兜底/补全工具。搜索空结果时读整章，或搜索结果间有信息空白时按范围补全。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "ReadChapter"
    description: str = load_desc(Path(__file__).parent / "chapter.md", {})
    params: type[ReadChapterParams] = ReadChapterParams

    def __init__(self, config: Config) -> None:
        """初始化 ReadChapter 工具。

        Args:
            config: 应用配置对象，包含数据库连接信息。

        Raises:
            SkipThisTool: 如果数据库连接未配置（密码为空），则跳过此工具。
        """
        super().__init__(config)

    @override
    async def __call__(self, params: ReadChapterParams) -> ToolReturnValue:
        """执行章节读取操作。

        在执行查询前确保数据库连接已建立，连接失败时返回 ToolError。

        Args:
            params: 读取参数，包含章节 ID 和可选的句子范围。

        Returns:
            操作结果，章节内容或错误信息。
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

            sentence_range = (
                [params.start, params.end]
                if params.start is not None and params.end is not None
                else None
            )
            result = await self._store.query_corpus(
                book=params.book,
                chapter_id=params.chapter_id,
                sentence_range=sentence_range,
            )
            return format_chapter_read_result(params.book, result)
        except SearchNovelError as e:
            return e.to_tool_error()
        except Exception as e:
            return ToolError(
                message=f"ReadChapter 内部错误: {e}",
                brief="内部错误",
            )
