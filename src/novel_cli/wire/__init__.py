"""Wire 模块初始化文件。

本模块提供 Wire 通道的实现，用于在 Soul 运行期间 Soul 与 UI 之间的通信。
Wire 是一个单生产者多消费者（SPMC）通道，支持消息合并和文件持久化。
"""

from __future__ import annotations

import asyncio
import contextlib
import copy

from kosong.message import MergeableMixin

from novel_cli.utils.aioqueue import Queue, QueueShutDown
from novel_cli.utils.broadcast import BroadcastQueue
from novel_cli.utils.logging import logger
from novel_cli.wire.file import WireFile
from novel_cli.wire.types import ContentPart, ToolCallPart, WireMessage, is_wire_message

WireMessageQueue = BroadcastQueue[WireMessage]


class Wire:
    """Wire 通道，用于 Soul 运行期间 Soul 与 UI 之间的通信。

    Wire 是一个单生产者多消费者（SPMC）通道，支持：
    - 原始消息队列和合并消息队列
    - 消息合并优化（将可合并的消息尽可能合并）
    - 文件后端持久化（可选）

    Attributes:
        _raw_queue: 原始消息广播队列。
        _merged_queue: 合并后的消息广播队列。
        _soul_side: Soul 端的消息发送器。
        _recorder: 消息记录器（可选，用于文件持久化）。
    """

    def __init__(self, *, file_backend: WireFile | None = None):
        self._raw_queue = WireMessageQueue()
        self._merged_queue = WireMessageQueue()

        self._soul_side = WireSoulSide(self._raw_queue, self._merged_queue)

        if file_backend is not None:
            # 将所有完整的 Wire 消息记录到文件后端
            self._recorder = _WireRecorder(file_backend, self._merged_queue.subscribe())
        else:
            self._recorder = None

    @property
    def soul_side(self) -> WireSoulSide:
        """获取 Wire 的 Soul 端。

        Returns:
            Soul 端的消息发送器。
        """
        return self._soul_side

    def ui_side(self, *, merge: bool) -> WireUISide:
        """创建 Wire 的 UI 端。

        Args:
            merge: 是否尽可能合并 Wire 消息。

        Returns:
            UI 端的消息接收器。
        """
        if merge:
            return WireUISide(self._merged_queue.subscribe())
        else:
            return WireUISide(self._raw_queue.subscribe())

    def shutdown(self) -> None:
        """关闭 Wire 通道，停止消息分发。"""
        self.soul_side.flush()
        logger.debug("Shutting down wire")
        self._raw_queue.shutdown()
        self._merged_queue.shutdown()

    async def join(self) -> None:
        """等待 Wire 记录器完成所有消息写入。

        如果存在文件后端记录器，等待其完成所有待处理的消息写入。
        """
        if self._recorder is None:
            return
        try:
            await self._recorder.join()
        except Exception:
            logger.exception("Wire recorder failed to flush:")


class WireSoulSide:
    """Wire 的 Soul 端，用于发送消息。

    Soul 端负责将消息发送到 Wire 通道，并支持消息合并优化。
    对于可合并的消息（MergeableMixin），会尽可能合并后发送。

    Attributes:
        _raw_queue: 原始消息广播队列。
        _merged_queue: 合并后的消息广播队列。
        _merge_buffer: 当前合并缓冲区。
    """

    def __init__(self, raw_queue: WireMessageQueue, merged_queue: WireMessageQueue):
        self._raw_queue = raw_queue
        self._merged_queue = merged_queue
        self._merge_buffer: MergeableMixin | None = None

    def send(self, msg: WireMessage) -> None:
        """发送 Wire 消息。

        将消息发送到原始队列，并根据消息类型进行合并处理后发送到合并队列。

        Args:
            msg: 要发送的 Wire 消息。
        """
        if not isinstance(msg, ContentPart | ToolCallPart):
            logger.debug("Sending wire message: {msg}", msg=msg)

        # 发送原始消息
        try:
            self._raw_queue.publish_nowait(msg)
        except QueueShutDown:
            logger.info("Failed to send raw wire message, queue is shut down: {msg}", msg=msg)

        # 合并发送合并消息
        match msg:
            case MergeableMixin():
                if self._merge_buffer is None:
                    self._merge_buffer = copy.deepcopy(msg)
                elif self._merge_buffer.merge_in_place(msg):
                    pass
                else:
                    self.flush()
                    self._merge_buffer = copy.deepcopy(msg)
            case _:
                self.flush()
                self._send_merged(msg)

    def flush(self) -> None:
        """刷新合并缓冲区，发送缓冲的消息。"""
        buffer = self._merge_buffer
        if buffer is None:
            return
        assert is_wire_message(buffer)
        self._send_merged(buffer)
        self._merge_buffer = None

    def _send_merged(self, msg: WireMessage) -> None:
        """发送合并后的消息到合并队列。

        Args:
            msg: 要发送的 Wire 消息。
        """
        try:
            self._merged_queue.publish_nowait(msg)
        except QueueShutDown:
            logger.info("Failed to send merged wire message, queue is shut down: {msg}", msg=msg)


class WireUISide:
    """Wire 的 UI 端，用于接收消息。

    UI 端负责从 Wire 通道接收消息。

    Attributes:
        _queue: 消息接收队列。
    """

    def __init__(self, queue: Queue[WireMessage]):
        self._queue = queue

    async def receive(self) -> WireMessage:
        """异步接收 Wire 消息。

        Returns:
            接收到的 Wire 消息。
        """
        msg = await self._queue.get()
        if not isinstance(msg, ContentPart | ToolCallPart):
            logger.debug("Receiving wire message: {msg}", msg=msg)
        return msg


class _WireRecorder:
    """Wire 消息记录器（内部类）。

    负责将 Wire 消息持久化到文件后端。

    Attributes:
        _wire_file: Wire 文件后端。
        _task: 消费循环异步任务。
    """

    def __init__(self, wire_file: WireFile, queue: Queue[WireMessage]) -> None:
        self._wire_file = wire_file
        self._task = asyncio.create_task(self._consume_loop(queue))

    async def join(self) -> None:
        """等待记录器完成任务。"""
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _consume_loop(self, queue: Queue[WireMessage]) -> None:
        """消息消费循环，持续从队列中获取消息并记录。

        Args:
            queue: 消息队列。
        """
        while True:
            try:
                msg = await queue.get()
                await self._record(msg)
            except QueueShutDown:
                break

    async def _record(self, msg: WireMessage) -> None:
        """记录消息到文件。

        Args:
            msg: 要记录的 Wire 消息。
        """
        await self._wire_file.append_message(msg)
