"""Wire 消息广播中心模块。

本模块提供会话级别的 Wire 消息广播中心，用于处理非轮转顺序的消息分发。
"""

from __future__ import annotations

from novel_cli.utils.aioqueue import Queue
from novel_cli.utils.broadcast import BroadcastQueue
from novel_cli.wire.types import WireMessage


class RootWireHub:
    """会话级别的 Wire 消息广播中心。

    用于在会话中广播非轮转顺序的 Wire 消息，支持多订阅者模式。

    Attributes:
        _queue: 内部广播队列，用于消息分发。
    """

    def __init__(self) -> None:
        self._queue = BroadcastQueue[WireMessage]()

    def subscribe(self) -> Queue[WireMessage]:
        """订阅广播中心的消息。

        Returns:
            新创建的订阅队列，用于接收广播消息。
        """
        return self._queue.subscribe()

    def unsubscribe(self, queue: Queue[WireMessage]) -> None:
        """取消订阅广播中心。

        Args:
            queue: 要取消的订阅队列。
        """
        self._queue.unsubscribe(queue)

    async def publish(self, msg: WireMessage) -> None:
        """异步发布消息到广播中心。

        Args:
            msg: 要发布的 Wire 消息。
        """
        await self._queue.publish(msg)

    def publish_nowait(self, msg: WireMessage) -> None:
        """同步发布消息到广播中心（非阻塞）。

        Args:
            msg: 要发布的 Wire 消息。
        """
        self._queue.publish_nowait(msg)

    def shutdown(self) -> None:
        """关闭广播中心，停止所有消息分发。"""
        self._queue.shutdown()
