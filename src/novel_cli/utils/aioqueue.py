"""异步队列工具。

本模块提供支持关闭功能的异步队列实现，兼容 Python 3.13 的 asyncio.QueueShutDown。
对于较低版本的 Python，提供等效的关闭支持实现。
"""

from __future__ import annotations

import asyncio
import sys

if sys.version_info >= (3, 13):
    QueueShutDown = asyncio.QueueShutDown  # type: ignore[assignment]

    class Queue[T](asyncio.Queue[T]):
        """支持关闭功能的 asyncio 队列。"""

else:

    class QueueShutDown(Exception):
        """在已关闭的队列上执行操作时抛出的异常。"""

    class _Shutdown:
        """队列关闭的哨兵标记。"""

    _SHUTDOWN = _Shutdown()

    class Queue[T](asyncio.Queue[T | _Shutdown]):
        """支持关闭功能的 asyncio 队列（Python < 3.13 版本）。

        Attributes:
            _shutdown: 队列是否已关闭的标记。
        """

        def __init__(self) -> None:
            """初始化队列。"""
            super().__init__()
            self._shutdown = False

        def shutdown(self, immediate: bool = False) -> None:
            """关闭队列。

            Args:
                immediate: 是否立即清空队列中待处理的项目。
            """
            if self._shutdown:
                return
            self._shutdown = True
            if immediate:
                self._queue.clear()

            getters = list(getattr(self, "_getters", []))
            count = max(1, len(getters))
            self._enqueue_shutdown(count)

        def _enqueue_shutdown(self, count: int) -> None:
            """将关闭哨兵入队。

            Args:
                count: 要入队的关闭哨兵数量。
            """
            for _ in range(count):
                try:
                    super().put_nowait(_SHUTDOWN)
                except asyncio.QueueFull:
                    self._queue.clear()
                    super().put_nowait(_SHUTDOWN)

        async def get(self) -> T:
            """异步获取队列中的项目。

            Returns:
                队列中的下一个项目。

            Raises:
                QueueShutDown: 队列已关闭且为空时抛出。
            """
            if self._shutdown and self.empty():
                raise QueueShutDown
            item = await super().get()
            if isinstance(item, _Shutdown):
                raise QueueShutDown
            return item

        def get_nowait(self) -> T:
            """同步获取队列中的项目（不阻塞）。

            Returns:
                队列中的下一个项目。

            Raises:
                QueueShutDown: 队列已关闭且为空时抛出。
            """
            if self._shutdown and self.empty():
                raise QueueShutDown
            item = super().get_nowait()
            if isinstance(item, _Shutdown):
                raise QueueShutDown
            return item

        async def put(self, item: T) -> None:
            """异步将项目放入队列。

            Args:
                item: 要放入队列的项目。

            Raises:
                QueueShutDown: 队列已关闭时抛出。
            """
            if self._shutdown:
                raise QueueShutDown
            await super().put(item)

        def put_nowait(self, item: T) -> None:
            """同步将项目放入队列（不阻塞）。

            Args:
                item: 要放入队列的项目。

            Raises:
                QueueShutDown: 队列已关闭时抛出。
            """
            if self._shutdown:
                raise QueueShutDown
            super().put_nowait(item)