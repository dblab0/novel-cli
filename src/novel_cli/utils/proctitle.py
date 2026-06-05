"""进程标题设置工具。

本模块提供设置操作系统进程标题和终端窗口标题的功能，
用于在进程管理器和终端标签页中显示可识别的名称。
"""

from __future__ import annotations

import sys


def set_process_title(title: str) -> None:
    """设置操作系统级别的进程标题，显示在 ps/top/终端面板中。

    Args:
        title: 要设置的进程标题。
    """
    try:
        import setproctitle

        setproctitle.setproctitle(title)
    except ImportError:
        pass


def set_terminal_title(title: str) -> None:
    """通过 ANSI OSC 转义序列设置终端标签/窗口标题。

    仅在 stderr 是 TTY 时写入，以避免污染管道输出。

    Args:
        title: 要设置的终端标题。
    """
    if not sys.stderr.isatty():
        return
    try:
        sys.stderr.write(f"\033]0;{title}\007")
        sys.stderr.flush()
    except OSError:
        pass


def init_process_name(name: str = "Novel Code") -> None:
    """初始化进程名称：设置 OS 进程标题和终端标签标题。

    Args:
        name: 进程名称，默认为 "Novel Code"。
    """
    set_process_title(name)
    set_terminal_title(name)