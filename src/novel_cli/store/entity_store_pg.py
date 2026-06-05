"""实体存储模块，通过 PostgreSQL + pgvector 实现实体的向量搜索和名称搜索功能。"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_cli.store.models import Entity
from novel_cli.utils.logging import logger

if TYPE_CHECKING:
    import asyncpg
    from novel_cli.config import NovelDBConfig


def _extract_name_from_id(entity_id: str) -> str:
    """从实体 ID 中提取实体名称。

    例如：凡人修仙传_人物_韩立_0 -> 韩立。

    Args:
        entity_id: 实体的唯一标识符。

    Returns:
        提取的实体名称，如果格式不符则返回原始 ID。
    """
    parts = entity_id.split("_")
    if len(parts) >= 4:
        return parts[-2]
    return entity_id


class EntityStorePG:
    """基于 PostgreSQL + pgvector 的实体搜索类。

    提供向量语义搜索和名称/别名精确搜索能力。

    Attributes:
        _pool: asyncpg 连接池实例。
        _config: 数据库配置实例。
        _vector_type: 向量类型，超过 2000 维使用 halfvec（HNSW 索引限制）。
    """

    def __init__(self, pool: asyncpg.Pool, config: NovelDBConfig) -> None:
        """初始化实体存储实例。

        Args:
            pool: asyncpg 连接池。
            config: 数据库配置对象。
        """
        self._pool = pool
        self._config = config
        # 超过 2000 维使用 halfvec（HNSW 索引限制）
        self._vector_type = "halfvec" if config.embedding_dim > 2000 else "vector"

    async def search_by_vector(
        self,
        query: str,
        book: str | None = None,
        top_k: int = 5,
    ) -> list[Entity]:
        """通过 pgvector 进行向量语义搜索。

        Args:
            query: 查询文本。
            book: 书名过滤条件，None 表示不过滤。
            top_k: 返回结果数量上限，默认 5。

        Returns:
            Entity 列表，包含向量搜索匹配结果及其相似度分数。
        """
        try:
            from novel_cli.store._embedding import get_embedding

            vector = await get_embedding(
                text=query,
                api_url=self._config.embedding_api_url,
                api_key=self._config.embedding_api_key,
                model=self._config.embedding_model,
            )
        except Exception:
            logger.warning("Failed to get embedding for query, returning empty results")
            return []

        # asyncpg 不原生支持 pgvector，编码为字符串并在 SQL 中转换类型
        query_vector = "[" + ",".join(str(v) for v in vector) + "]"

        sql = f"""
        SELECT e.id, e.name, e.type, e.book, e.description, e.alias,
               1 - (emb.embedding <=> $1::{self._vector_type}) AS score
        FROM entity_embeddings emb
        JOIN entities e ON emb.id = e.id
        WHERE ($2::text IS NULL OR e.book = $2)
        ORDER BY emb.embedding <=> $1::{self._vector_type}
        LIMIT $3
        """
        try:
            rows = await self._pool.fetch(sql, query_vector, book, top_k)
        except Exception as e:
            logger.error("pgvector search failed: {e}", e=e)
            return []

        entities: list[Entity] = []
        for r in rows:
            name = r["name"] or _extract_name_from_id(r["id"])
            entities.append(
                Entity(
                    id=r["id"],
                    name=name,
                    type=r["type"],
                    book=r["book"],
                    description=r["description"] or "",
                    score=float(r["score"]),
                    match_type="vector",
                    alias=r["alias"] or "",
                )
            )
        return entities

    async def search_by_name(
        self,
        name: str,
        book: str | None = None,
    ) -> list[Entity]:
        """通过 SQL 进行名称/别名精确搜索。

        Args:
            name: 待搜索的名称。
            book: 书名过滤条件，None 表示不过滤。

        Returns:
            Entity 列表，包含名称或别名匹配结果。
        """
        sql = """
        SELECT id, name, type, book, description, alias
        FROM entities
        WHERE (name = $1 OR alias @> to_jsonb($1::text))
          AND ($2::text IS NULL OR book = $2)
        """
        rows = await self._pool.fetch(sql, name, book)
        entities: list[Entity] = []
        for r in rows:
            entities.append(
                Entity(
                    id=r["id"],
                    name=r["name"],
                    type=r["type"],
                    book=r["book"],
                    description=r["description"] or "",
                    score=None,
                    match_type="name",
                    alias=r["alias"] or "",
                )
            )
        return entities