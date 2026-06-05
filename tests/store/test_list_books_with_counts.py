"""Store 测试。

验证 NovelStore.list_books_with_counts() 返回值。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.anyio
async def test_list_books_with_counts() -> None:
    """list_books_with_counts 查询正确并返回 (书名, 章节数) 列表。"""
    from novel_cli.store import NovelStore

    # 模拟 asyncpg Pool
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = [
        {"book": "三国演义", "cnt": 120},
        {"book": "西游记", "cnt": 100},
    ]

    store = NovelStore.__new__(NovelStore)
    store._pool = mock_pool

    result = await store.list_books_with_counts()

    assert result == [("三国演义", 120), ("西游记", 100)]
    mock_pool.fetch.assert_called_once_with(
        "SELECT book, COUNT(*) as cnt FROM documents GROUP BY book ORDER BY book"
    )


@pytest.mark.anyio
async def test_list_books_with_counts_empty() -> None:
    """数据库中无书籍时返回空列表。"""
    from novel_cli.store import NovelStore

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []

    store = NovelStore.__new__(NovelStore)
    store._pool = mock_pool

    result = await store.list_books_with_counts()

    assert result == []
