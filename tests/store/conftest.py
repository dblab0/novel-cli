"""Store test fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_cli.store.models import (
    CorpusResult,
    CorpusSentence,
    Entity,
    GraphRelation,
    GraphResult,
    RelType,
)


# === Data model fixtures ===


@pytest.fixture
def sample_entities() -> list[Entity]:
    """Construct test entity list."""
    return [
        Entity(
            id="凡人修仙传_人物_韩立_0",
            name="韩立",
            type="人物",
            book="凡人修仙传",
            description="韩立是凡人修仙传的主角，性格谨慎，擅长隐匿",
            score=0.85,
            match_type="vector",
        ),
        Entity(
            id="凡人修仙传_法宝_掌天瓶_0",
            name="掌天瓶",
            type="法宝",
            book="凡人修仙传",
            description="掌天瓶是韩立的核心法宝，可催熟灵药",
            score=0.72,
            match_type="vector",
        ),
    ]


@pytest.fixture
def sample_rel_types() -> list[RelType]:
    """Construct test relationship types."""
    return [
        RelType(type="POSSESS", description="拥有关系", category="拥有"),
        RelType(type="USE", description="使用关系", category="使用"),
        RelType(type="MENTOR", description="师徒关系", category="师徒"),
    ]


@pytest.fixture
def sample_relations() -> list[GraphRelation]:
    """Construct test relationship data."""
    return [
        GraphRelation(
            relationship_type="POSSESS",
            other_node_id="凡人修仙传_法宝_掌天瓶_0",
            description="韩立拥有掌天瓶，这是他的核心法宝",
        ),
        GraphRelation(
            relationship_type="USE",
            other_node_id="凡人修仙传_功法_青元剑诀_0",
            description="韩立修炼青元剑诀作为本命功法",
        ),
    ]


@pytest.fixture
def sample_sentences() -> list[CorpusSentence]:
    """Construct test sentence list."""
    return [
        CorpusSentence(sentence_index=10, text="韩立从储物袋中取出一件物品"),
        CorpusSentence(sentence_index=11, text="掌天瓶在月光下散发出淡淡的光芒"),
        CorpusSentence(sentence_index=12, text="他小心翼翼地将灵力注入其中"),
    ]


@pytest.fixture
def sample_corpus_result() -> CorpusResult:
    """Construct test corpus result."""
    return CorpusResult(
        sentences=[],
        text="第173章内容：韩立从储物袋中取出掌天瓶...",
        title="第173章",
        hint="已返回完整章节内容",
        brief="第173章",
    )


@pytest.fixture
def sample_graph_result_types(sample_rel_types: list[RelType]) -> GraphResult:
    """Construct test graph result for types sub_action."""
    return GraphResult(types=sample_rel_types)


@pytest.fixture
def sample_graph_result_desc() -> GraphResult:
    """Construct test graph result for desc sub_action."""
    return GraphResult(description="韩立是凡人修仙传的主角，性格谨慎，擅长隐匿")


@pytest.fixture
def sample_graph_result_related(sample_relations: list[GraphRelation]) -> GraphResult:
    """Construct test graph result for related sub_action."""
    return GraphResult(related=sample_relations)


@pytest.fixture
def sample_graph_result_rels(sample_relations: list[GraphRelation]) -> GraphResult:
    """Construct test graph result for rels sub_action."""
    return GraphResult(rels=sample_relations)


# === Mock database fixtures ===


@pytest.fixture
def mock_pg_connection() -> AsyncMock:
    """Mock PostgreSQL connection."""
    conn = AsyncMock()
    conn.execute.return_value = MagicMock(fetchall=lambda: [])
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []
    return conn


@pytest.fixture
def mock_pg_pool(mock_pg_connection: AsyncMock) -> AsyncMock:
    """Mock PostgreSQL connection pool."""
    pool = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = mock_pg_connection
    pool.acquire.return_value.__aexit__.return_value = None
    pool.close.return_value = None
    pool.fetch.return_value = []
    pool.fetchrow.return_value = None
    return pool