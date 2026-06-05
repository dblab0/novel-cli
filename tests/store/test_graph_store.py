"""Tests for GraphStorePG."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from novel_cli.store.graph_store_pg import GraphStorePG


class TestGraphStorePGCreation:
    """Tests for GraphStorePG creation."""

    def test_graph_store_pg_creation(self) -> None:
        """Test GraphStorePG creation."""
        pool = MagicMock()
        store = GraphStorePG(pool)

        assert store._pool is pool


class TestGraphStorePGQueryDispatch:
    """Tests for GraphStorePG query dispatch logic."""

    async def test_query_types_dispatch(self) -> None:
        """Test query dispatches to types."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        store = GraphStorePG(pool)

        result = await store.query("test_id", "types", book="凡人修仙传")
        assert result.types == []

    async def test_query_related_without_rel_types(self) -> None:
        """Test query related returns empty without rel_types."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        store = GraphStorePG(pool)

        result = await store.query("test_id", "related", rel_types=None, book="凡人修仙传")
        assert result.related == []

    async def test_query_rels_without_rel_types(self) -> None:
        """Test query rels returns empty without rel_types."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        store = GraphStorePG(pool)

        result = await store.query("test_id", "rels", rel_types=None, book="凡人修仙传")
        assert result.rels == []

    async def test_query_unknown_sub_action(self) -> None:
        """Test query with unknown sub_action raises ValueError."""
        pool = MagicMock()
        store = GraphStorePG(pool)

        try:
            await store.query("test_id", "unknown", rel_types=None, book="凡人修仙传")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
