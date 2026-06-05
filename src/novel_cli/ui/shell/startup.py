"""Shell 启动进度显示模块。

提供 Shell 初始化过程中的临时状态显示功能。
"""

from __future__ import annotations

from rich.status import Status

from novel_cli.ui.shell.console import console


class ShellStartupProgress:
    """Shell 启动过程中的临时状态显示。

    在 Shell 初始化期间显示进度信息，初始化完成后自动停止。

    Attributes:
        _enabled: 是否启用状态显示。
        _status: Rich Status 实例。
    """

    def __init__(self, *, enabled: bool | None = None) -> None:
        """初始化启动进度显示。

        Args:
            enabled: 是否启用，若为 None 则根据终端类型自动判断。
        """
        self._enabled = console.is_terminal if enabled is None else enabled
        self._status: Status | None = None

    def update(self, message: str) -> None:
        """更新状态消息。

        Args:
            message: 新的状态消息文本。
        """
        if not self._enabled:
            return

        status_message = f"[cyan]{message}[/cyan]"
        if self._status is None:
            self._status = console.status(status_message, spinner="dots")
            self._status.start()
            return

        self._status.update(status_message)

    def stop(self) -> None:
        """停止状态显示。"""
        if self._status is None:
            return

        self._status.stop()
        self._status = None
