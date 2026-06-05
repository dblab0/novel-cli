"""SearchGraph 工具：小说知识图谱查询工具。

提供 SearchGraph 工具，用于在小说知识库中查询实体关系图。
不传 rel_type 时返回实体描述 + 关系类型概览，传入 rel_type 时返回该类型的关系详情。
"""

import asyncio
from pathlib import Path
from typing import override

from novel_cli.config import Config

from kosong.tooling import ToolError, ToolReturnValue
from kosong.tooling.error import ToolValidateError
from kosong.utils.typing import JsonType
from pydantic import BaseModel, Field, ValidationError

from novel_cli.tools.novel._base import NovelToolBase
from novel_cli.tools.novel.errors import MissingParamError, SearchNovelError
from novel_cli.tools.novel.formatter import format_graph_overview, format_graph_rels
from novel_cli.tools.utils import load_desc


class SearchGraphParams(BaseModel):
    """小说知识图谱查询参数。

    Attributes:
        entity_id: 实体 ID，格式：书名_类型_名称_序号。
        book: 书名过滤（精确匹配）。
        rel_type: 关系类型。不传时返回实体描述 + 关系类型概览；
                  传入时返回该类型的关系详情（含描述和 chapter_ids）。
    """

    entity_id: str = Field(
        description=(
            'The entity ID to query, in the format "book_type_name_index" '
            "(e.g. 凡人修仙传_人物_韩立_0). Obtain it from SearchEntity results."
        ),
    )
    book: str | None = Field(
        default=None,
        description="Book name filter (exact match). Set by the system when a book is selected.",
    )
    rel_type: str | None = Field(
        default=None,
        description=(
            "Relationship type to query. Omit to get entity description + available types. "
            "Pass a type name from the overview to get relationship details. "
            "Supports comma-separated multiple types, e.g. '亲属,对立'."
        ),
    )


class SearchGraph(NovelToolBase[SearchGraphParams]):
    """小说知识图谱查询工具。

    不传 rel_type：返回实体描述 + 关系类型概览。
    传入 rel_type：返回该类型的关系详情（含描述和 chapter_ids）。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "SearchGraph"
    description: str = load_desc(Path(__file__).parent / "graph.md", {})
    params: type[SearchGraphParams] = SearchGraphParams

    def __init__(self, config: Config) -> None:
        """初始化 SearchGraph 工具。

        Args:
            config: 应用配置对象，包含数据库连接信息。

        Raises:
            SkipThisTool: 如果数据库连接未配置（密码为空），则跳过此工具。
        """
        super().__init__(config)

    async def call(self, arguments: JsonType) -> ToolReturnValue:
        """拦截原始参数，检测显式传入 rel_type=null 的情况。

        LLM 有时会显式传入 null 而非省略参数，Pydantic 会将 null 转为
        字段默认值 None，导致无法在 __call__ 中区分两种情况。
        因此在 call 层通过原始 arguments 字典提前检测并处理。

        Args:
            arguments: LLM 传入的原始参数字典。

        Returns:
            工具返回值，或错误信息。
        """
        if isinstance(arguments, dict) and "rel_type" in arguments and arguments["rel_type"] is None:
            return await self._handle_explicit_null(arguments)
        return await super().call(arguments)

    async def _handle_explicit_null(self, arguments: dict) -> ToolReturnValue:
        """处理 rel_type 被显式传入 null 的情况。

        验证其他参数，查询该书籍下所有可用关系类型，
        返回 ToolError 引导 agent 正确使用。

        Args:
            arguments: 原始参数字典。

        Returns:
            包含可用关系类型提示的 ToolError。
        """
        # 验证其他参数
        try:
            params = self.params.model_validate(arguments)
        except ValidationError as e:
            return ToolValidateError(str(e))

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

            types_result = await self._store.query_graph(
                entity_id=params.entity_id,
                sub_action="types",
                book=params.book,
            )

            if not types_result.types:
                return ToolError(
                    message=f"rel_type 不允许为 null。实体「{params.entity_id}」没有可用关系类型。",
                    brief="rel_type 不允许为 null",
                )

            type_lines = "\n".join(
                f"  【{rt.category}】{rt.type} — {rt.description}"
                for rt in types_result.types
            )
            return ToolError(
                message=(
                    f"rel_type 不允许为 null，请从以下关系类型中选择：\n"
                    f"{type_lines}\n\n"
                    f"用法: SearchGraph(entity_id='{params.entity_id}', rel_type='从上方选择')"
                ),
                brief=f"请选择关系类型（共 {len(types_result.types)} 种）",
            )
        except SearchNovelError as e:
            return e.to_tool_error()
        except Exception as e:
            return ToolError(
                message=f"SearchGraph 内部错误: {e}",
                brief="内部错误",
            )

    @override
    async def __call__(self, params: SearchGraphParams) -> ToolReturnValue:
        """执行知识图谱查询操作。

        在执行查询前确保数据库连接已建立，连接失败时返回 ToolError。

        Args:
            params: 查询参数，包含实体 ID 和可选的关系类型。

        Returns:
            操作结果，查询结果或错误信息。
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

            if params.rel_type:
                # 支持逗号分隔多个关系类型
                rel_types = [r.strip() for r in params.rel_type.split(",")]
                result = await self._store.query_graph(
                    entity_id=params.entity_id,
                    sub_action="rels",
                    rel_types=rel_types,
                    book=params.book,
                )
                return format_graph_rels(params.entity_id, rel_types, result)
            else:
                # 默认：描述 + 关系类型概览，并行查询
                desc_result, types_result = await asyncio.gather(
                    self._store.query_graph(
                        entity_id=params.entity_id,
                        sub_action="desc",
                        book=params.book,
                    ),
                    self._store.query_graph(
                        entity_id=params.entity_id,
                        sub_action="types",
                        book=params.book,
                    ),
                )
                return format_graph_overview(params.entity_id, desc_result, types_result)
        except SearchNovelError as e:
            return e.to_tool_error()
        except Exception as e:
            return ToolError(
                message=f"SearchGraph 内部错误: {e}",
                brief="内部错误",
            )
