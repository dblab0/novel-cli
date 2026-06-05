"""NovelStore.list_books() 测试。

测试 NovelStore 门面类的 list_books 方法，
验证数据库查询返回正确的书名列表。
"""

# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from novel_cli.config import NovelDBConfig
from novel_cli.store import NovelStore


@pytest.fixture
def mock_novel_store() -> NovelStore:
    """创建带有 mock 连接池的 NovelStore 实例。

    Returns:
        配置了 mock pool 的 NovelStore 实例，可直接用于测试。
    """
    config = NovelDBConfig()
    store = NovelStore(config)
    # Mock 连接池，不执行实际数据库连接
    store._pool = AsyncMock()
    return store


async def test_list_books(mock_novel_store: NovelStore) -> None:
    """测试 list_books 返回正确的书名列表。

    验证方法能正确解析数据库查询结果并返回书名列表。
    """
    # Mock 数据库查询返回
    mock_novel_store._pool.fetch.return_value = [
        {"book": "凡人修仙传"},
        {"book": "凡人修仙之仙界篇"},
    ]
    result = await mock_novel_store.list_books()
    assert result == ["凡人修仙传", "凡人修仙之仙界篇"]


async def test_list_books_empty(mock_novel_store: NovelStore) -> None:
    """测试空数据库返回空列表。

    验证当数据库中没有书籍数据时返回空列表而非抛出异常。
    """
    mock_novel_store._pool.fetch.return_value = []
    result = await mock_novel_store.list_books()
    assert result == []


async def test_list_books_single(mock_novel_store: NovelStore) -> None:
    """测试数据库中只有一本书时返回单元素列表。

    验证边界情况：单本书籍的正确处理。
    """
    mock_novel_store._pool.fetch.return_value = [
        {"book": "凡人修仙传"},
    ]
    result = await mock_novel_store.list_books()
    assert result == ["凡人修仙传"]


async def test_list_books_query_correct(mock_novel_store: NovelStore) -> None:
    """测试 list_books 使用正确的 SQL 查询。

    验证查询语句包含 DISTINCT 和 ORDER BY 子句以确保结果正确排序。
    """
    mock_novel_store._pool.fetch.return_value = []
    await mock_novel_store.list_books()

    # 验证调用参数
    mock_novel_store._pool.fetch.assert_called_once()
    call_args = mock_novel_store._pool.fetch.call_args
    sql = call_args[0][0]
    assert "SELECT DISTINCT book" in sql
    assert "FROM documents" in sql
    assert "ORDER BY book" in sql


async def test_list_books_without_pool_raises() -> None:
    """测试连接池未建立时调用 list_books 抛出 AssertionError。

    验证在未调用 connect() 时调用 list_books 的错误处理。
    """
    config = NovelDBConfig()
    store = NovelStore(config)
    # _pool 为 None，应触发 AssertionError
    with pytest.raises(AssertionError):
        await store.list_books()