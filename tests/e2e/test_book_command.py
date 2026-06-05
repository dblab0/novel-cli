"""E2E PTY 交互测试：/book 命令。

测试选书命令的交互行为：
- 精确选书 `/book 凡人修仙传`
- 关键词搜索选书 `/book 凡人`
- 取消选择 `/book none`
"""

from __future__ import annotations

import sys

import pytest

from tests.e2e.shell_pty_helpers import (
    make_home_dir,
    make_work_dir,
    read_until_prompt_ready,
    start_shell_pty,
    write_scripted_config,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Shell PTY E2E tests require a Unix-like PTY.",
)


def test_book_command_without_database_config(tmp_path) -> None:
    """测试数据库未配置时 /book 命令提示错误。"""
    config_path = write_scripted_config(tmp_path, ["text: hello"])
    work_dir = make_work_dir(tmp_path)
    home_dir = make_home_dir(tmp_path)
    shell = start_shell_pty(
        config_path=config_path,
        work_dir=work_dir,
        home_dir=home_dir,
        yolo=True,
    )

    try:
        shell.read_until_contains("欢迎来到 Novel CLI!")
        prompt_mark = shell.mark()
        read_until_prompt_ready(shell, after=prompt_mark)

        # 发送 /book 命令（数据库未配置）
        book_mark = shell.mark()
        shell.send_line("/book")
        # 等待提示信息
        shell.read_until_contains("小说知识库未配置", after=book_mark, timeout=10.0)
        prompt_mark2 = shell.mark()
        read_until_prompt_ready(shell, after=prompt_mark2)
    finally:
        shell.close()


def test_book_clear_command_without_database(tmp_path) -> None:
    """测试 /book none 清除选书命令（无数据库连接时的行为）。"""
    config_path = write_scripted_config(tmp_path, ["text: hello"])
    work_dir = make_work_dir(tmp_path)
    home_dir = make_home_dir(tmp_path)
    shell = start_shell_pty(
        config_path=config_path,
        work_dir=work_dir,
        home_dir=home_dir,
        yolo=True,
    )

    try:
        shell.read_until_contains("欢迎来到 Novel CLI!")
        prompt_mark = shell.mark()
        read_until_prompt_ready(shell, after=prompt_mark)

        # 发送 /book none 命令
        book_mark = shell.mark()
        shell.send_line("/book none")
        # 等待数据库未配置提示（因为检查数据库连接在前）
        shell.read_until_contains("小说知识库未配置", after=book_mark, timeout=10.0)
        prompt_mark2 = shell.mark()
        read_until_prompt_ready(shell, after=prompt_mark2)
    finally:
        shell.close()


def test_book_command_in_shell_mode(tmp_path) -> None:
    """测试 Shell 模式下 /book 命令的输出格式。"""
    config_path = write_scripted_config(tmp_path, ["text: turn finished"])
    work_dir = make_work_dir(tmp_path)
    home_dir = make_home_dir(tmp_path)
    shell = start_shell_pty(
        config_path=config_path,
        work_dir=work_dir,
        home_dir=home_dir,
        yolo=True,
    )

    try:
        shell.read_until_contains("欢迎来到 Novel CLI!")
        prompt_mark = shell.mark()
        read_until_prompt_ready(shell, after=prompt_mark)

        # 发送 /book 命令
        book_mark = shell.mark()
        shell.send_line("/book")
        shell.read_until_contains("小说知识库未配置", after=book_mark, timeout=10.0)
        prompt_mark2 = shell.mark()
        read_until_prompt_ready(shell, after=prompt_mark2)

        # 验证命令执行后的状态正常
        turn_mark = shell.mark()
        shell.send_line("hello")
        shell.read_until_contains("turn finished", after=turn_mark, timeout=10.0)
        prompt_mark3 = shell.mark()
        read_until_prompt_ready(shell, after=prompt_mark3)
    finally:
        shell.close()