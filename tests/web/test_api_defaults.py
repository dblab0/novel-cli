"""API 端点测试。

验证 /api/defaults 和 /api/books 端点的行为。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from novel_cli.web.defaults import WebDefaults


@pytest.fixture
def defaults() -> WebDefaults:
    """创建测试用的 WebDefaults 实例。"""
    return WebDefaults(
        agent_file="/path/to/agent.yaml",
        book_name="西游记",
        work_dir="/home/test/work",
    )


# ─── WebDefaults 单元测试 ────────────────────────────────────


def test_web_defaults_serialization(defaults: WebDefaults) -> None:
    """WebDefaults 序列化为 JSON 包含所有字段。"""
    data = defaults.model_dump()
    assert data["agent_file"] == "/path/to/agent.yaml"
    assert data["book_name"] == "西游记"
    assert data["work_dir"] == "/home/test/work"


def test_web_defaults_none_values() -> None:
    """WebDefaults 仅 work_dir 为必填，其余默认 None。"""
    d = WebDefaults(work_dir="/tmp")
    assert d.agent_file is None
    assert d.book_name is None
    assert d.work_dir == "/tmp"


# ─── /api/defaults 端点测试 ──────────────────────────────────


@pytest.mark.anyio
async def test_get_defaults_returns_stored_values(defaults: WebDefaults) -> None:
    """GET /api/defaults 返回 app.state.defaults 的值。"""
    from novel_cli.web.api.sessions import get_defaults

    # 模拟 FastAPI Request 对象，使 request.app.state.defaults 可访问
    class FakeState:
        defaults: WebDefaults

    class FakeApp:
        state: FakeState

    class FakeRequest:
        app: FakeApp

    fake_state = FakeState()
    fake_state.defaults = defaults
    fake_app = FakeApp()
    fake_app.state = fake_state
    fake_request = FakeRequest()
    fake_request.app = fake_app

    result = await get_defaults(fake_request)  # type: ignore[arg-type]
    assert result == defaults.model_dump()


# ─── /api/books 端点测试 ──────────────────────────────────────


@pytest.mark.anyio
async def test_list_books_no_db_password() -> None:
    """数据库未配置（无密码）时返回空列表。"""
    from novel_cli.web.api.sessions import list_books

    class FakeRequest:
        pass

    with patch("novel_cli.config.load_config") as mock_config:
        mock_conf = mock_config.return_value
        mock_conf.services.novel_db.pg_password = SecretStr("")
        result = await list_books(FakeRequest())  # type: ignore[arg-type]
    assert result == []


@pytest.mark.anyio
async def test_list_books_with_mock_db() -> None:
    """有数据库配置时返回书籍列表。"""
    from novel_cli.web.api.sessions import list_books

    class FakeRequest:
        pass

    mock_store = AsyncMock()
    mock_store.list_books_with_counts.return_value = [
        ("西游记", 100),
        ("三国演义", 120),
    ]

    with (
        patch("novel_cli.config.load_config") as mock_config,
        patch("novel_cli.store.NovelStore", return_value=mock_store),
    ):
        mock_conf = mock_config.return_value
        mock_conf.services.novel_db.pg_password = SecretStr("secret")
        result = await list_books(FakeRequest())  # type: ignore[arg-type]

    assert len(result) == 2
    assert result[0].name == "西游记"
    assert result[0].chapter_count == 100
    assert result[1].name == "三国演义"
    assert result[1].chapter_count == 120
    mock_store.connect.assert_called_once()
    mock_store.close.assert_called_once()
