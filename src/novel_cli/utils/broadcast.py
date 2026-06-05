"""广播队列工具。

本模块提供支持多订阅者的广播队列实现，允许多个消费者同时接收发布的消息。
"""

import asyncio

from novel_cli.utils.aioqueue import Queue


class BroadcastQueue[T]:
    """广播队列，允许多个订阅者接收发布的消息。

    Attributes:
        _queues: 订阅者队列集合。
    """

    def __init__(self) -> None:
        """初始化广播队列。"""
        self._queues: set[Queue[T]] = set()

    def subscribe(self) -> Queue[T]:
        """创建新的订阅队列。

        Returns:
            用于接收广播消息的新队列实例。
        """
        queue: Queue[T] = Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: Queue[T]) -> None:
        """移除订阅队列。

        Args:
            queue: 要移除的订阅队列。
        """
        self._queues.discard(queue)

    async def publish(self, item: T) -> None:
        """向所有订阅队列发布消息。

        Args:
            item: 要发布的消息。
        """
        await asyncio.gather(*(queue.put(item) for queue in self._queues))

    def publish_nowait(self, item: T) -> None:
        """向所有订阅队列发布消息（不等待）。

        Args:
            item: 要发布的消息。
        """
        for queue in self._queues:
            queue.put_nowait(item)

    def shutdown(self, immediate: bool = False) -> None:
        """关闭所有订阅队列。

        Args:
            immediate: 是否立即清空队列中待处理的消息。
        """
        for queue in self._queues:
            queue.shutdown(immediate=immediate)
        self._queues.clear()