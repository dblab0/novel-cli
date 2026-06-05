"""Integration tests for SearchEntity, SearchGraph, SearchCorpus tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from inline_snapshot import snapshot
from pydantic import SecretStr

from novel_cli.config import Config, NovelDBConfig, get_default_config
from novel_cli.store import NovelStore
from novel_cli.store.models import CorpusResult, Entity, GraphResult
from novel_cli.tools import SkipThisTool
from novel_cli.tools.novel import SearchEntity, SearchEntityParams, SearchGraph, SearchGraphParams, SearchCorpus, SearchCorpusParams
from novel_cli.tools.novel.errors import (
    DBConnectionError,
    EntityNotFoundError,
)
from kosong.tooling import ToolError


# === SearchEntity tests ===


async def test_entity_search_success(
    search_entity_tool: SearchEntity, mock_store: AsyncMock
) -> None:
    """Test successful entity search."""
    result = await search_entity_tool(
        SearchEntityParams(query="韩立")
    )

    assert not result.is_error
    assert "韩立" in result.output
    assert "凡人修仙传_人物_韩立_0" in result.output
    mock_store.search_entities.assert_called_once_with(
        query="韩立", book=None, top_k=5
    )


async def test_entity_search_empty_result(
    search_entity_tool: SearchEntity, mock_store: AsyncMock
) -> None:
    """Test entity search with empty result."""
    mock_store.search_entities.return_value = []

    result = await search_entity_tool(
        SearchEntityParams(query="不存在的角色")
    )

    assert not result.is_error  # Empty result is not an error
    assert "未找到" in result.output


async def test_entity_search_empty_query(search_entity_tool: SearchEntity) -> None:
    """Test entity mode with empty query string."""
    result = await search_entity_tool(SearchEntityParams(query=""))

    assert result.is_error
    assert result.brief == snapshot("参数缺失: query")


async def test_entity_search_by_name(
    search_entity_tool: SearchEntity, mock_store: AsyncMock
) -> None:
    """Test entity search with search_mode='name' calls search_entities_by_name."""
    result = await search_entity_tool(
        SearchEntityParams(query="韩立", search_mode="name")
    )

    assert not result.is_error
    assert "韩立" in result.output
    mock_store.search_entities_by_name.assert_called_once_with(
        name="韩立", book=None
    )


# === SearchGraph tests ===


async def test_graph_rels(
    search_graph_tool: SearchGraph, mock_store: AsyncMock
) -> None:
    """测试传入 rel_type 返回关系详情。"""
    result = await search_graph_tool(
        SearchGraphParams(
            entity_id="凡人修仙传_人物_韩立_0",
            rel_type="POSSESS",
            book="凡人修仙传",
        )
    )

    assert not result.is_error
    # 验证 mock store 被正确调用
    mock_store.query_graph.assert_called()


async def test_graph_missing_book(search_graph_tool: SearchGraph) -> None:
    """测试 graph 模式缺少 book 参数。"""
    result = await search_graph_tool(
        SearchGraphParams(
            entity_id="凡人修仙传_人物_韩立_0",
        )
    )

    assert result.is_error
    assert result.brief == snapshot("请使用 /book 选择书籍")


# === SearchCorpus tests ===


async def test_corpus_chapter(
    search_corpus_tool: SearchCorpus, mock_store: AsyncMock
) -> None:
    """测试按章节 ID 搜索。"""
    result = await search_corpus_tool(
        SearchCorpusParams(book="凡人修仙传", keyword="掌天瓶", chapter_ids=[173])
    )

    assert not result.is_error
    assert "第173章内容" in result.output


async def test_corpus_keyword(
    search_corpus_tool: SearchCorpus, mock_store: AsyncMock
) -> None:
    """测试关键词搜索。"""
    result = await search_corpus_tool(
        SearchCorpusParams(
            book="凡人修仙传",
            keyword="掌天瓶",
            chapter_ids=[173],
            context_size=2,
        )
    )

    assert not result.is_error
    mock_store.query_corpus.assert_called_once()


async def test_corpus_missing_book(search_corpus_tool: SearchCorpus) -> None:
    """测试缺少 book 参数时返回错误。"""
    result = await search_corpus_tool(
        SearchCorpusParams(keyword="test")
    )

    assert result.is_error
    assert result.brief == snapshot("请使用 /book 选择书籍")


# === error propagation tests ===


async def test_store_raises_db_connection_error(
    search_entity_tool: SearchEntity, mock_store: AsyncMock
) -> None:
    """Test DB connection error propagation from store."""
    mock_store.search_entities.side_effect = DBConnectionError(
        message="小说知识库连接失败: Connection refused",
        brief="数据库连接失败",
    )

    result = await search_entity_tool(
        SearchEntityParams(query="韩立")
    )

    assert result.is_error
    assert "数据库连接失败" in result.brief
    assert "连接" in result.message


async def test_store_raises_entity_not_found(
    search_graph_tool: SearchGraph, mock_store: AsyncMock
) -> None:
    """Test entity not found error propagation from store."""
    mock_store.query_graph.side_effect = EntityNotFoundError(
        message='未找到实体「不存在的实体_0」',
        brief="实体未找到",
    )

    result = await search_graph_tool(
        SearchGraphParams(
            entity_id="不存在的实体_0",
            book="凡人修仙传",
        )
    )

    assert result.is_error
    assert result.brief == snapshot("实体未找到")


# === parameter boundary tests ===


async def test_top_k_default_value(
    search_entity_tool: SearchEntity, mock_store: AsyncMock
) -> None:
    """Test top_k default value is 5."""
    await search_entity_tool(SearchEntityParams(query="test"))
    call_kwargs = mock_store.search_entities.call_args[1]
    assert call_kwargs["top_k"] == 5


async def test_context_size_default_value(
    search_corpus_tool: SearchCorpus, mock_store: AsyncMock
) -> None:
    """测试 context_size 默认值为 1。"""
    await search_corpus_tool(
        SearchCorpusParams(book="凡人修仙传", keyword="test")
    )
    call_kwargs = mock_store.query_corpus.call_args[1]
    assert call_kwargs["context_size"] == 1


async def test_graph_overview_without_rel_type(
    search_graph_tool: SearchGraph, mock_store: AsyncMock
) -> None:
    """不传 rel_type 应返回概览（描述 + 关系类型列表）。"""
    result = await search_graph_tool(
        SearchGraphParams(
            entity_id="凡人修仙传_人物_韩立_0",
            book="凡人修仙传",
        )
    )

    assert not result.is_error
    # 概览应包含关系类型
    assert "POSSESS" in result.output or "MENTOR" in result.output


# === internal error handling ===


async def test_internal_error_handling(
    search_entity_tool: SearchEntity, mock_store: AsyncMock
) -> None:
    """Test handling of unexpected internal errors."""
    mock_store.search_entities.side_effect = RuntimeError("Unexpected error")

    result = await search_entity_tool(
        SearchEntityParams(query="韩立")
    )

    assert result.is_error
    assert "内部错误" in result.brief
    assert "Unexpected error" in result.message


# === SkipThisTool 模式和懒连接行为测试 ===


def test_search_entity_skip_when_no_config() -> None:
    """当 pg_password 为空时，构造 SearchEntity 应抛出 SkipThisTool。"""
    conf = get_default_config()
    # 默认 pg_password 为空字符串，确认一下
    assert not conf.services.novel_db.pg_password.get_secret_value()

    import pytest

    with pytest.raises(SkipThisTool):
        SearchEntity(config=conf)


async def test_search_entity_lazy_connect() -> None:
    """首次 __call__ 时建立连接，后续调用不重复连接。"""
    conf = get_default_config()
    conf.services.novel_db.pg_password = SecretStr("test-password")

    tool = SearchEntity(config=conf)

    # 首次调用前 _store 为 None
    assert tool._store is None

    # Mock NovelStore.connect 和 search_entities 方法
    mock_store = AsyncMock(spec=NovelStore)
    mock_store.search_entities = AsyncMock(return_value=[])
    mock_store.connect = AsyncMock()

    with patch.object(NovelStore, "__new__", return_value=mock_store):
        # 首次调用，应触发连接
        result = await tool(SearchEntityParams(query="韩立"))
        mock_store.connect.assert_called_once()

        # 第二次调用，不应再次连接
        await tool(SearchEntityParams(query="韩立"))
        mock_store.connect.assert_called_once()


async def test_search_entity_connect_failure_returns_tool_error() -> None:
    """连接失败时 __call__ 应返回 ToolError 而非抛出异常。"""
    conf = get_default_config()
    conf.services.novel_db.pg_password = SecretStr("test-password")

    tool = SearchEntity(config=conf)

    # Mock NovelStore 构造后 connect 抛出异常
    mock_store = AsyncMock(spec=NovelStore)
    mock_store.connect = AsyncMock(side_effect=ConnectionRefusedError("连接被拒绝"))

    with patch.object(NovelStore, "__new__", return_value=mock_store):
        result = await tool(SearchEntityParams(query="韩立"))

    assert result.is_error
    assert "数据库连接失败" in result.brief


# === 缺 book 错误提示测试 ===


async def test_search_graph_book_error_message(
    search_graph_tool: SearchGraph, mock_store: AsyncMock
) -> None:
    """验证 graph 模式缺 book 时错误提示包含 /book 命令指引。

    确保错误提示明确告知用户使用 /book 命令选择书籍。
    """
    # graph 模式缺 book
    result = await search_graph_tool(
        SearchGraphParams(entity_id="test_entity")
    )
    assert result.is_error
    # 错误提示应包含 /book 命令指引
    assert "/book" in result.brief or "请使用 /book 选择书籍" in result.brief


async def test_search_corpus_book_error_message(
    search_corpus_tool: SearchCorpus, mock_store: AsyncMock
) -> None:
    """验证 corpus 模式缺 book 时错误提示包含 /book 命令指引。

    确保错误提示明确告知用户使用 /book 命令选择书籍。
    """
    # corpus 模式缺 book
    result = await search_corpus_tool(
        SearchCorpusParams(keyword="test")
    )
    assert result.is_error
    # 错误提示应包含 /book 命令指引
    assert "/book" in result.brief or "请使用 /book 选择书籍" in result.brief


# === SearchGraph rel_type=null 显式传入测试 ===


async def test_graph_explicit_null_rel_type_returns_error(
    search_graph_tool: SearchGraph, mock_store: AsyncMock
) -> None:
    """显式传入 rel_type=null 应返回错误，列出可用关系类型。"""
    result = await search_graph_tool.call({
        "entity_id": "凡人修仙传_人物_韩立_0",
        "book": "凡人修仙传",
        "rel_type": None,
    })

    assert result.is_error
    # 应包含可用关系类型名称
    assert "POSSESS" in result.message
    assert "USE" in result.message
    # 应包含引导信息
    assert "rel_type" in result.message
    assert "SearchGraph(" in result.message


async def test_graph_explicit_null_rel_type_no_types(
    search_graph_tool: SearchGraph, mock_store: AsyncMock
) -> None:
    """显式传入 rel_type=null 且无可用关系类型时，应返回无类型提示。"""
    mock_store.query_graph.return_value = MagicMock(types=[], description="", rels=[])

    result = await search_graph_tool.call({
        "entity_id": "凡人修仙传_人物_韩立_0",
        "book": "凡人修仙传",
        "rel_type": None,
    })

    assert result.is_error
    assert "没有可用关系类型" in result.message


async def test_graph_explicit_null_rel_type_missing_book(
    search_graph_tool: SearchGraph,
) -> None:
    """显式传入 rel_type=null 且缺少 book，应返回 /book 指引。"""
    result = await search_graph_tool.call({
        "entity_id": "凡人修仙传_人物_韩立_0",
        "rel_type": None,
    })

    assert result.is_error
    assert "/book" in result.brief or "请使用 /book 选择书籍" in result.brief


async def test_graph_omit_rel_type_still_overview(
    search_graph_tool: SearchGraph, mock_store: AsyncMock
) -> None:
    """不传 rel_type（字典中无该 key）应走正常概览流程，不受影响。"""
    result = await search_graph_tool.call({
        "entity_id": "凡人修仙传_人物_韩立_0",
        "book": "凡人修仙传",
    })

    assert not result.is_error
