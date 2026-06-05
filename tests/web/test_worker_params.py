"""Worker 参数传递测试。

验证 Worker 从 session state 读取 agent_file 和 current_book 并传给 NovelCLI.create。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from novel_cli.session import Session as NovelCLISession
from novel_cli.session_state import SessionState


def _make_mock_session(
    tmp_path: Path,
    agent_file: str | None = None,
    current_book: str | None = None,
) -> MagicMock:
    """创建一个模拟的 NovelCLI Session 对象。"""
    session_dir = tmp_path / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    state = SessionState(agent_file=agent_file, current_book=current_book)
    (session_dir / "state.json").write_text(state.model_dump_json(), encoding="utf-8")

    mock_session = MagicMock(spec=NovelCLISession)
    mock_session.dir = session_dir
    mock_session.state = state
    return mock_session


@pytest.mark.anyio
async def test_worker_passes_agent_file_and_book_name(tmp_path: Path) -> None:
    """Worker 从 session state 读取 agent_file 和 current_book 传给 NovelCLI.create。"""
    agent_file = tmp_path / "agent.yaml"
    agent_file.write_text("name: test", encoding="utf-8")

    mock_session = _make_mock_session(
        tmp_path,
        agent_file=str(agent_file),
        current_book="西游记",
    )

    mock_joint = MagicMock()
    mock_joint.novel_cli_session = mock_session

    mock_novel_cli = AsyncMock()

    with (
        patch(
            "novel_cli.web.runner.worker.load_session_by_id",
            return_value=mock_joint,
        ),
        patch(
            "novel_cli.web.runner.worker.get_global_mcp_config_file",
            return_value=Path("/nonexistent"),
        ),
        patch(
            "novel_cli.web.runner.worker.NovelCLI.create",
            new_callable=AsyncMock,
            return_value=mock_novel_cli,
        ) as mock_create,
    ):
        from novel_cli.web.runner.worker import run_worker

        await run_worker(uuid4())

        # 验证 NovelCLI.create 被调用时传入了正确的参数
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["agent_file"] == agent_file
        assert call_kwargs["book_name"] == "西游记"


@pytest.mark.anyio
async def test_worker_no_agent_file_no_book(tmp_path: Path) -> None:
    """session state 无 agent_file 和 current_book 时传 None。"""
    mock_session = _make_mock_session(tmp_path)

    mock_joint = MagicMock()
    mock_joint.novel_cli_session = mock_session

    mock_novel_cli = AsyncMock()

    with (
        patch(
            "novel_cli.web.runner.worker.load_session_by_id",
            return_value=mock_joint,
        ),
        patch(
            "novel_cli.web.runner.worker.get_global_mcp_config_file",
            return_value=Path("/nonexistent"),
        ),
        patch(
            "novel_cli.web.runner.worker.NovelCLI.create",
            new_callable=AsyncMock,
            return_value=mock_novel_cli,
        ) as mock_create,
    ):
        from novel_cli.web.runner.worker import run_worker

        await run_worker(uuid4())

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["agent_file"] is None
        assert call_kwargs["book_name"] is None


@pytest.mark.anyio
async def test_worker_agent_file_deleted_exits(tmp_path: Path) -> None:
    """agent_file 指向的文件已被删除时 Worker 以非零状态退出。"""
    mock_session = _make_mock_session(
        tmp_path,
        agent_file="/nonexistent/agent.yaml",
        current_book=None,
    )

    mock_joint = MagicMock()
    mock_joint.novel_cli_session = mock_session

    with (
        patch(
            "novel_cli.web.runner.worker.load_session_by_id",
            return_value=mock_joint,
        ),
        patch(
            "novel_cli.web.runner.worker.get_global_mcp_config_file",
            return_value=Path("/nonexistent"),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        from novel_cli.web.runner.worker import run_worker

        await run_worker(uuid4())

    assert exc_info.value.code == 1
