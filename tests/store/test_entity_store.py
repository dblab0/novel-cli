"""Tests for EntityStorePG."""

from __future__ import annotations

from novel_cli.store.entity_store_pg import _extract_name_from_id


class TestExtractNameFromId:
    """Tests for _extract_name_from_id helper."""

    def test_extract_name_standard_format(self) -> None:
        """Test extracting name from standard entity ID."""
        name = _extract_name_from_id("凡人修仙传_人物_韩立_0")
        assert name == "韩立"

    def test_extract_name_with_multiple_parts(self) -> None:
        """Test extracting name from ID with multiple parts."""
        name = _extract_name_from_id("凡人修仙传_法宝_掌天瓶_0")
        assert name == "掌天瓶"

    def test_extract_name_short_id(self) -> None:
        """Test extracting name from short ID returns original."""
        name = _extract_name_from_id("韩立")
        assert name == "韩立"

    def test_extract_name_three_parts(self) -> None:
        """Test extracting name from ID with only 3 parts."""
        name = _extract_name_from_id("凡人修仙传_人物_韩立")
        # With 3 parts, len(parts) >= 4 is False, so returns original
        assert name == "凡人修仙传_人物_韩立"


class TestEntityStorePGCreation:
    """Tests for EntityStorePG creation and basic operations."""

    def test_entity_store_pg_creation(self) -> None:
        """Test EntityStorePG creation."""
        from unittest.mock import MagicMock

        from novel_cli.config import NovelDBConfig
        from novel_cli.store.entity_store_pg import EntityStorePG

        config = NovelDBConfig()
        pool = MagicMock()
        store = EntityStorePG(pool, config)

        assert store._config == config
        assert store._pool is pool