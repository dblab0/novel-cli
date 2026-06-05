"""图存储模块，通过 PostgreSQL 实现知识图谱查询功能。"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from novel_cli.store.models import GraphRelation, GraphResult, RelType

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


class GraphStorePG:
    """基于 PostgreSQL 的知识图谱查询类。

    提供实体关系类型查询、描述查询、关联实体查询等能力。

    Attributes:
        _pool: asyncpg 连接池实例。
        _data_dir: 关系类型元数据根目录，包含 {book}/relationship_meta.json。
        _rel_meta_cache: 按 book 缓存的关系类型元数据。
    """

    def __init__(self, pool: asyncpg.Pool, data_dir: Path | None = None) -> None:
        """初始化图存储实例。

        Args:
            pool: asyncpg 连接池。
            data_dir: 关系类型元数据根目录（如 data/input_data），
                      为 None 时跳过描述加载。
        """
        self._pool = pool
        self._data_dir = data_dir
        self._rel_meta_cache: dict[str, dict[str, list[str]]] = {}

    def _load_rel_meta(self, book: str) -> dict[str, list[str]]:
        """加载并缓存指定书籍的关系类型元数据。

        从 data_dir/{book}/relationship_meta.json 读取，格式为：
        {"归属": {"description": ["加入/参与/效力"], "category": ["人物与组织"]}}

        Args:
            book: 书名。

        Returns:
            关系类型名称到描述列表的映射，如 {"归属": ["加入/参与/效力"]}。
            文件不存在时返回空字典。
        """
        if book in self._rel_meta_cache:
            return self._rel_meta_cache[book]

        meta: dict[str, list[str]] = {}
        if self._data_dir is None:
            self._rel_meta_cache[book] = meta
            return meta

        meta_path = self._data_dir / book / "relationship_meta.json"
        if not meta_path.is_file():
            logger.debug("关系类型元数据文件不存在: %s", meta_path)
            self._rel_meta_cache[book] = meta
            return meta

        try:
            raw: dict[str, dict] = json.loads(meta_path.read_text("utf-8"))
            for rel_name, rel_info in raw.items():
                descs = rel_info.get("description", [])
                if isinstance(descs, list) and descs:
                    meta[rel_name] = descs
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("关系类型元数据解析失败: %s", meta_path)

        self._rel_meta_cache[book] = meta
        return meta

    async def query(
        self,
        entity_id: str,
        sub_action: str,
        rel_types: list[str] | None = None,
        book: str = "",
    ) -> GraphResult:
        """统一查询入口，根据 sub_action 分发到具体查询方法。

        Args:
            entity_id: 实体的唯一标识符。
            sub_action: 子查询动作，支持 "types"、"desc"、"related"、"rels"。
            rel_types: 关系类型过滤列表，用于 "related" 和 "rels" 查询。
            book: 书名过滤条件。

        Returns:
            GraphResult 对象，包含查询结果。

        Raises:
            ValueError: 未知的 sub_action 时抛出。
        """
        if sub_action == "types":
            return GraphResult(types=await self.query_types(entity_id, book))
        elif sub_action == "desc":
            desc, alias = await self.query_description(entity_id, book)
            return GraphResult(description=desc, alias=alias)
        elif sub_action == "related":
            return GraphResult(related=await self.query_related(entity_id, rel_types or [], book))
        elif sub_action == "rels":
            return GraphResult(rels=await self.query_relationships(entity_id, rel_types or [], book))
        raise ValueError(f"Unknown sub_action: {sub_action}")

    async def query_types(self, entity_id: str, book: str) -> list[RelType]:
        """查询实体的所有关系类型（双向），包含分类和描述信息。

        描述从 relationship_meta.json 加载，多个描述用 / 拼接。

        Args:
            entity_id: 实体的唯一标识符。
            book: 书名过滤条件。

        Returns:
            关系类型列表，每个元素包含 type、description 和 category 字段。
        """
        sql = """
        SELECT DISTINCT r.rel_type, r.category
        FROM relationships r
        WHERE (r.source_id = $1 OR r.target_id = $1)
          AND r.book = $2
        """
        rows = await self._pool.fetch(sql, entity_id, book)
        meta = self._load_rel_meta(book)
        return [
            RelType(
                type=r["rel_type"],
                description="/".join(meta.get(r["rel_type"], [])),
                category=r["category"] or "",
            )
            for r in rows
        ]

    async def query_description(self, entity_id: str, book: str) -> tuple[str, str]:
        """查询实体描述和别名。

        Args:
            entity_id: 实体的唯一标识符。
            book: 书名过滤条件。

        Returns:
            (描述文本, 别名) 元组，未找到时均返回空字符串。
        """
        sql = "SELECT description, alias FROM entities WHERE id = $1 AND book = $2"
        row = await self._pool.fetchrow(sql, entity_id, book)
        if row:
            return row["description"], row["alias"] or ""
        return "", ""

    async def query_related(
        self, entity_id: str, rel_types: list[str], book: str
    ) -> list[GraphRelation]:
        """查询关联实体（双向，不含描述）。

        Args:
            entity_id: 实体的唯一标识符。
            rel_types: 关系类型过滤列表。
            book: 书名过滤条件。

        Returns:
            GraphRelation 列表，包含关系类型和关联实体 ID。
        """
        sql = """
        SELECT
            rel_type,
            CASE WHEN source_id = $1 THEN target_id ELSE source_id END AS other_id
        FROM relationships
        WHERE (source_id = $1 OR target_id = $1)
          AND rel_type = ANY($2)
          AND book = $3
        """
        rows = await self._pool.fetch(sql, entity_id, rel_types, book)
        return [
            GraphRelation(relationship_type=r["rel_type"], other_node_id=r["other_id"])
            for r in rows
        ]

    async def query_relationships(
        self, entity_id: str, rel_types: list[str], book: str
    ) -> list[GraphRelation]:
        """查询关系详情（双向，含描述）。

        Args:
            entity_id: 实体的唯一标识符。
            rel_types: 关系类型过滤列表。
            book: 书名过滤条件。

        Returns:
            GraphRelation 列表，包含关系类型、关联实体 ID 和描述。
        """
        sql = """
        SELECT
            rel_type,
            CASE WHEN source_id = $1 THEN target_id ELSE source_id END AS other_id,
            r.description
        FROM relationships r
        WHERE (source_id = $1 OR target_id = $1)
          AND rel_type = ANY($2)
          AND r.book = $3
        """
        rows = await self._pool.fetch(sql, entity_id, rel_types, book)
        return [
            GraphRelation(
                relationship_type=r["rel_type"],
                other_node_id=r["other_id"],
                description=r["description"] or "",
            )
            for r in rows
        ]
