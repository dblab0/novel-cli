"""pipe 模式循环检测异常退出测试。

参照 test_print_exit_code.py 模式，验证 Print.run() 对 ToolCallLoopDetected 异常的处理。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from novel_cli.cli import ExitCode
from novel_cli.soul.toolset import ToolCallLoopDetected
from novel_cli.ui.print import Print


def _make_soul() -> AsyncMock:
    soul = AsyncMock()
    soul.runtime = None
    return soul


def _make_print(soul: AsyncMock, tmp_path: Path) -> Print:
    return Print(
        soul=soul,
        input_format="text",
        output_format="text",
        context_file=tmp_path / "context.json",
    )


def test_loop_detection_returns_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pipe 模式收到 ToolCallLoopDetected 返回 FAILURE 退出码。"""
    soul = _make_soul()
    p = _make_print(soul, tmp_path)

    async def _raise_loop(*args, **kwargs):
        raise ToolCallLoopDetected("read_file", 5)

    monkeypatch.setattr("novel_cli.ui.print.run_soul", _raise_loop)
    code = asyncio.run(p.run(command="hello"))
    assert code == ExitCode.FAILURE
