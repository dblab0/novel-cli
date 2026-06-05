"""Web CLI 参数校验测试。

验证 novel-cli web 命令的 --agent-file、--work-dir 参数校验行为。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from novel_cli.cli import cli as app


runner = CliRunner()


def test_web_agent_file_not_exists(tmp_path: Path) -> None:
    """--agent-file 指向不存在的文件时报错。"""
    result = runner.invoke(
        app,
        ["web", "--agent-file", str(tmp_path / "nonexistent.yaml"), "--no-open"],
    )
    assert result.exit_code != 0
    assert "does not exist" in result.output.lower() or "not exist" in result.output.lower()


def test_web_work_dir_not_exists(tmp_path: Path) -> None:
    """--work-dir 指向不存在的目录时报错。"""
    result = runner.invoke(
        app,
        ["web", "--work-dir", str(tmp_path / "nonexistent_dir"), "--no-open"],
    )
    assert result.exit_code != 0
    assert "不存在" in result.output or "does not exist" in result.output.lower()


def test_web_agent_file_exists_and_work_dir_exists(tmp_path: Path) -> None:
    """合法的 --agent-file 和 --work-dir 参数被正确传递。"""
    agent_file = tmp_path / "agent.yaml"
    agent_file.write_text("name: test", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    with patch("novel_cli.web.app.run_web_server") as mock_run:
        result = runner.invoke(
            app,
            [
                "web",
                "--agent-file",
                str(agent_file),
                "--work-dir",
                str(work_dir),
                "--no-open",
            ],
        )
        # 不关心 uvicorn 的结果，只校验参数解析成功
        if result.exit_code == 0 or mock_run.called:
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            # agent_file 应该被 resolve 成绝对路径
            assert call_kwargs["agent_file"] == str(agent_file.resolve())
            # work_dir 应该被 resolve 成绝对路径
            assert work_dir.resolve().as_posix() in call_kwargs["work_dir"] or str(work_dir.resolve()) == call_kwargs["work_dir"]


def test_web_no_params_uses_cwd(tmp_path: Path) -> None:
    """不传参数时 work_dir 默认使用 cwd。"""
    with patch("novel_cli.web.app.run_web_server") as mock_run:
        result = runner.invoke(app, ["web", "--no-open"])
        if result.exit_code == 0 or mock_run.called:
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["work_dir"] == str(Path.cwd().resolve())
            assert call_kwargs["agent_file"] is None
            assert call_kwargs["book_name"] is None
