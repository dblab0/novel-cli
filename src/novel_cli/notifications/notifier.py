"""通知轮询监视器模块。

提供异步轮询通知并回调处理的机制。
"""

import asyncio
from collections.abc import Awaitable, Callable

from novel_cli.utils.logging import logger

from .manager import NotificationManager
from .models import NotificationSink, NotificationView


class NotificationWatcher:
    """通知监视器，负责轮询并投递待处理通知。

    Attributes:
        _manager: 通知管理器实例。
        _sink: 目标 Sink 名称。
        _on_notification: 通知回调函数。
        _before_poll: 轮询前的回调函数。
        _interval_s: 轮询间隔（秒）。
    """

    def __init__(
        self,
        manager: NotificationManager,
        *,
        sink: NotificationSink,
        on_notification: Callable[[NotificationView], Awaitable[None] | None],
        before_poll: Callable[[], object] | None = None,
        interval_s: float = 1.0,
    ) -> None:
        """初始化通知监视器。

        Args:
            manager: 通知管理器实例。
            sink: 目标 Sink 名称。
            on_notification: 通知回调函数。
            before_poll: 轮询前的回调函数，可选。
            interval_s: 轮询间隔（秒），默认 1.0。
        """
        self._manager = manager
        self._sink = sink
        self._on_notification = on_notification
        self._before_poll = before_poll
        self._interval_s = interval_s

    async def poll_once(self) -> list[NotificationView]:
        """执行一次轮询，投递待处理通知。

        Returns:
            已投递的通知视图列表。
        """
        return await self._manager.deliver_pending(
            self._sink,
            on_notification=self._on_notification,
            before_claim=self._before_poll,
        )

    async def run_forever(self) -> None:
        """持续轮询通知直到被取消。"""
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NotificationWatcher poll failed")
            await asyncio.sleep(self._interval_s)
