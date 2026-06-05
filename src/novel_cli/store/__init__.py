"""NovelStore 模块，提供实体、图谱和语料存储的统一门面（基于 PostgreSQL）。"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_cli.store.corpus_store import CorpusStore
from novel_cli.store.entity_store_pg import EntityStorePG
from novel_cli.store.graph_store_pg import GraphStorePG
from novel_cli.store.models import (
    CorpusResult,
    CorpusSentence,
    Entity,
    GraphRelation,
    GraphResult,
    RelType,
)

if TYPE_CHECKING:
    import asyncpg
    from novel_cli.config import NovelDBConfig

__all__ = [
    "NovelStore",
    "EntityStorePG",
    "GraphStorePG",
    "CorpusStore",
    "Entity",
    "GraphRelation",
    "GraphResult",
    "RelType",
    "CorpusResult",
    "CorpusSentence",
]


class NovelStore:
    """小说存储统一门面类，管理 PostgreSQL 连接池并提供各子存储的访问接口。

    Attributes:
        _config: 数据库配置实例。
        _pool: asyncpg 连接池实例，初始化后可用。
        _entity_store: 实体存储实例，用于向量/名称搜索。
        _graph_store: 图存储实例，用于知识图谱查询。
        _corpus_store: 语料存储实例，用于原文检索。
    """

    def __init__(self, config: NovelDBConfig) -> None:
        """初始化小说存储门面实例。

        Args:
            config: 数据库配置对象。
        """
        self._config = config
        self._pool: asyncpg.Pool | None = None
        self._entity_store: EntityStorePG | None = None
        self._graph_store: GraphStorePG | None = None
        self._corpus_store: CorpusStore | None = None

    async def connect(self) -> None:
        """建立共享的 PostgreSQL 连接池。

        创建连接池后，初始化所有子存储并共享同一连接池。
        """
        import asyncpg

        self._pool = await asyncpg.create_pool(
            host=self._config.pg_host,
            port=self._config.pg_port,
            database=self._config.pg_db,
            user=self._config.pg_user,
            password=self._config.pg_password.get_secret_value(),
            min_size=5,
            max_size=self._config.pg_pool_size,
        )

        # 解析 data_dir：优先使用配置值，为空时回退到 cwd/data/input_data
        from pathlib import Path

        data_dir: Path | None = None
        if self._config.data_dir:
            data_dir = Path(self._config.data_dir)
        else:
            candidate = Path.cwd() / "data" / "input_data"
            if candidate.is_dir():
                data_dir = candidate

        # 在所有子存储间共享同一连接池
        self._entity_store = EntityStorePG(self._pool, self._config)
        self._graph_store = GraphStorePG(self._pool, data_dir=data_dir)
        self._corpus_store = CorpusStore(self._pool)

    async def close(self) -> None:
        """关闭共享的连接池。

        关闭连接池后，所有子存储将无法继续使用。
        """
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def list_books(self) -> list[str]:
        """查询知识库中所有可用的书名列表。

        通过查询 documents 表获取所有已入库的书名。

        Returns:
            按名称排序的书名列表。

        Raises:
            AssertionError: 连接未建立时抛出。
        """
        assert self._pool is not None
        rows = await self._pool.fetch(
            "SELECT DISTINCT book FROM documents ORDER BY book"
        )
        return [r["book"] for r in rows]

    async def list_books_with_counts(self) -> list[tuple[str, int]]:
        """查询所有书名及其章节数。

        Returns:
            (书名, 章节数) 列表，按书名排序。

        Raises:
            AssertionError: 连接未建立时抛出。
        """
        assert self._pool is not None
        rows = await self._pool.fetch(
            "SELECT book, COUNT(*) as cnt FROM documents GROUP BY book ORDER BY book"
        )
        return [(r["book"], r["cnt"]) for r in rows]

    async def search_entities(
        self,
        query: str,
        book: str | None = None,
        top_k: int = 5,
    ) -> list[Entity]:
        """实体向量语义搜索。

        Args:
            query: 查询文本。
            book: 书名过滤条件，None 表示不过滤。
            top_k: 返回结果数量上限，默认 5。

        Returns:
            Entity 列表，包含向量搜索匹配结果。

        Raises:
            AssertionError: 连接未建立时抛出。
        """
        assert self._entity_store is not None
        return await self._entity_store.search_by_vector(query, book, top_k)

    async def search_entities_by_name(
        self,
        name: str,
        book: str | None = None,
    ) -> list[Entity]:
        """实体名称/别名精确搜索。

        Args:
            name: 待搜索的名称。
            book: 书名过滤条件，None 表示不过滤。

        Returns:
            Entity 列表，包含名称匹配结果。

        Raises:
            AssertionError: 连接未建立时抛出。
        """
        assert self._entity_store is not None
        return await self._entity_store.search_by_name(name, book)

    async def query_graph(
        self,
        entity_id: str,
        sub_action: str,
        rel_types: list[str] | None = None,
        book: str = "",
    ) -> GraphResult:
        """知识图谱查询。

        Args:
            entity_id: 实体的唯一标识符。
            sub_action: 子查询动作，支持 "types"、"desc"、"related"、"rels"。
            rel_types: 关系类型过滤列表。
            book: 书名过滤条件。

        Returns:
            GraphResult 对象，包含图谱查询结果。

        Raises:
            AssertionError: 连接未建立时抛出。
        """
        assert self._graph_store is not None
        return await self._graph_store.query(entity_id, sub_action, rel_types, book)

    async def query_corpus(
        self,
        book: str,
        chapter_id: int | None = None,
        chapter_ids: list[int] | None = None,
        keywords: list[str] | None = None,
        context_size: int = 1,
        sentence_range: list[int] | None = None,
    ) -> CorpusResult:
        """小说语料检索。

        Args:
            book: 书名。
            chapter_id: 章节 ID，用于获取完整章节或指定句子范围。
            chapter_ids: 章节 ID 列表，用于关键词搜索时限定范围。
            keywords: 关键词列表，用于搜索包含关键词的句子。
            context_size: 关键词搜索时的上下文句子数量，默认 1。
            sentence_range: 句子索引范围 [start, end]，用于获取指定范围的句子。

        Returns:
            CorpusResult 对象，包含语料检索结果。

        Raises:
            AssertionError: 连接未建立时抛出。
        """
        assert self._corpus_store is not None
        return await self._corpus_store.query(
            book,
            chapter_id=chapter_id,
            chapter_ids=chapter_ids,
            keywords=keywords,
            context_size=context_size,
            sentence_range=sentence_range,
        )