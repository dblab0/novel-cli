"""信号处理工具。

本模块提供跨平台的 SIGINT 信号处理功能，兼容 Unix 和 Windows 系统。
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable


def install_sigint_handler(
    loop: asyncio.AbstractEventLoop, handler: Callable[[], None]
) -> Callable[[], None]:
    """安装跨平台的 SIGINT 信号处理器。

    在 Unix 事件循环中优先使用 `loop.add_signal_handler`。
    在 Windows（或其他不支持的平台）上回退到 `signal.signal`。
    回退方式无法从循环中移除，但卸载时会恢复之前的处理器。

    Args:
        loop: asyncio 事件循环。
        handler: 信号触发时调用的处理函数。

    Returns:
        移除已安装处理器的函数。调用该函数保证不会抛出异常。
    """

    try:
        loop.add_signal_handler(signal.SIGINT, handler)

        def remove() -> None:
            with contextlib.suppress(RuntimeError):
                loop.remove_signal_handler(signal.SIGINT)

        return remove
    except RuntimeError:
        # Windows ProactorEventLoop 和某些环境不支持 add_signal_handler
        # 使用同步信号处理作为回退方案
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda signum, frame: handler())

        def remove() -> None:
            with contextlib.suppress(RuntimeError):
                signal.signal(signal.SIGINT, previous)

        return remove