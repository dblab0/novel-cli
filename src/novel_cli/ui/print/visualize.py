"""可视化输出模块。

本模块提供多种打印器（Printer）实现，用于将 Wire 协议消息格式化输出。
支持文本格式和 JSON 流式格式的输出，以及仅输出最终结果的模式。

主要组件：
    Printer：打印器协议，定义 feed 和 flush 接口。
    TextPrinter：文本格式打印器，使用 rich 库直接打印消息。
    JsonPrinter：JSON 流式打印器，将消息序列化为 JSON 输出。
    FinalOnlyTextPrinter：仅输出最终结果的文本打印器。
    FinalOnlyJsonPrinter：仅输出最终结果的 JSON 打印器。
"""

from typing import Protocol

import rich
from kosong.message import Message

from novel_cli.cli import OutputFormat
from novel_cli.soul.message import tool_result_to_message
from novel_cli.utils.aioqueue import QueueShutDown
from novel_cli.wire import Wire
from novel_cli.wire.types import (
    ContentPart,
    Notification,
    PlanDisplay,
    StepBegin,
    StepInterrupted,
    ToolCall,
    ToolCallPart,
    ToolResult,
    WireMessage,
)


class Printer(Protocol):
    """打印器协议，定义消息输出接口。

    所有打印器必须实现 feed 和 flush 方法。
    """

    def feed(self, msg: WireMessage) -> None:
        """接收并处理一条 Wire 消息。

        Args:
            msg: 要处理的 Wire 消息。
        """
        ...

    def flush(self) -> None:
        """刷新缓冲区，输出所有未处理的消息。"""
        ...


def _merge_content(buffer: list[ContentPart], part: ContentPart) -> None:
    """将内容部分合并到缓冲区中。

    尝试将新内容部分与缓冲区最后一个元素就地合并，
    如果无法合并则追加到缓冲区末尾。

    Args:
        buffer: 内容部分缓冲区。
        part: 要合并的新内容部分。
    """
    if not buffer or not buffer[-1].merge_in_place(part):
        buffer.append(part)


class TextPrinter(Printer):
    """文本格式打印器。

    使用 rich 库直接打印 Wire 消息，适用于调试和简单输出场景。
    """

    def feed(self, msg: WireMessage) -> None:
        """接收并打印一条 Wire 消息。

        Args:
            msg: 要打印的 Wire 消息。
        """
        rich.print(msg)

    def flush(self) -> None:
        """刷新缓冲区。文本打印器无需刷新操作。"""
        pass


class JsonPrinter(Printer):
    """JSON 流式打印器。

    将 Wire 消息序列化为 JSON 格式输出。缓冲内容部分和工具调用，
    在适当时机刷新输出完整消息。

    Attributes:
        _content_buffer: 内容部分缓冲区，用于合并连续的内容部分。
        _tool_call_buffer: 工具调用缓冲区，存储当前助手消息的工具调用。
        _pending_notifications: 待发送的通知列表，缓冲直到当前助手消息达到安全边界。
        _last_tool_call: 最后一个工具调用，用于合并工具调用部分。
    """

    def __init__(self) -> None:
        self._content_buffer: list[ContentPart] = []
        """内容部分缓冲区，用于合并连续的内容部分。"""
        self._tool_call_buffer: list[ToolCall] = []
        """工具调用缓冲区，存储当前助手消息的工具调用。"""
        self._pending_notifications: list[Notification] = []
        """待发送的通知列表，缓冲直到当前助手消息达到安全边界。"""
        self._last_tool_call: ToolCall | None = None

    def feed(self, msg: WireMessage) -> None:
        """接收并处理一条 Wire 消息。

        根据消息类型执行相应处理：
        - StepBegin/StepInterrupted：刷新缓冲区
        - Notification：缓冲或直接输出通知
        - ContentPart：合并到内容缓冲区
        - ToolCall：添加到工具调用缓冲区
        - ToolCallPart：合并到最后一个工具调用
        - ToolResult/PlanDisplay：刷新并输出

        Args:
            msg: 要处理的 Wire 消息。
        """
        match msg:
            case StepBegin() | StepInterrupted():
                self.flush()
            case Notification() as notification:
                if self._content_buffer or self._tool_call_buffer:
                    self._pending_notifications.append(notification)
                else:
                    self._flush_assistant_message()
                    self._flush_notifications()
                    self._emit_notification(notification)
            case ContentPart() as part:
                # 尽可能与之前的内容部分合并
                _merge_content(self._content_buffer, part)
            case ToolCall() as call:
                self._tool_call_buffer.append(call)
                self._last_tool_call = call
            case ToolCallPart() as part:
                if self._last_tool_call is None:
                    return
                assert self._last_tool_call.merge_in_place(part)
            case ToolResult() as result:
                self._flush_assistant_message()
                self._flush_notifications()
                message = tool_result_to_message(result)
                print(message.model_dump_json(exclude_none=True), flush=True)
            case PlanDisplay() as plan:
                self._flush_assistant_message()
                self._flush_notifications()
                print(plan.model_dump_json(exclude_none=True), flush=True)
            case _:
                # 忽略其他消息
                pass

    def _flush_assistant_message(self) -> None:
        """刷新助手消息缓冲区。

        将缓冲区中的内容部分和工具调用合并为助手消息并输出。
        """
        if not self._content_buffer and not self._tool_call_buffer:
            return

        message = Message(
            role="assistant",
            content=self._content_buffer,
            tool_calls=self._tool_call_buffer or None,
        )
        print(message.model_dump_json(exclude_none=True), flush=True)

        self._content_buffer.clear()
        self._tool_call_buffer.clear()
        self._last_tool_call = None

    def _emit_notification(self, notification: Notification) -> None:
        """输出单个通知消息。

        Args:
            notification: 要输出的通知。
        """
        print(notification.model_dump_json(exclude_none=True), flush=True)

    def _flush_notifications(self) -> None:
        """刷新所有待发送的通知。"""
        for notification in self._pending_notifications:
            self._emit_notification(notification)
        self._pending_notifications.clear()

    def flush(self) -> None:
        """刷新缓冲区，输出所有未处理的消息。"""
        self._flush_assistant_message()
        self._flush_notifications()


class FinalOnlyTextPrinter(Printer):
    """仅输出最终结果的文本打印器。

    在 StepBegin 或 StepInterrupted 时清空缓冲区，
    仅在最终 flush 时输出文本内容。适用于只关心最终结果的场景。

    Attributes:
        _content_buffer: 内容部分缓冲区。
    """

    def __init__(self) -> None:
        self._content_buffer: list[ContentPart] = []

    def feed(self, msg: WireMessage) -> None:
        """接收并缓冲 Wire 消息。

        步骤开始或中断时清空缓冲区，内容部分则合并到缓冲区。

        Args:
            msg: 要处理的 Wire 消息。
        """
        match msg:
            case StepBegin() | StepInterrupted():
                self._content_buffer.clear()
            case ContentPart() as part:
                _merge_content(self._content_buffer, part)
            case _:
                pass

    def flush(self) -> None:
        """刷新缓冲区，输出最终文本内容。"""
        if not self._content_buffer:
            return
        message = Message(role="assistant", content=self._content_buffer)
        text = message.extract_text()
        if text:
            print(text, flush=True)
        self._content_buffer.clear()


class FinalOnlyJsonPrinter(Printer):
    """仅输出最终结果的 JSON 打印器。

    在 StepBegin 或 StepInterrupted 时清空缓冲区，
    仅在最终 flush 时输出 JSON 格式的消息。适用于只关心最终结果的场景。

    Attributes:
        _content_buffer: 内容部分缓冲区。
    """

    def __init__(self) -> None:
        self._content_buffer: list[ContentPart] = []

    def feed(self, msg: WireMessage) -> None:
        """接收并缓冲 Wire 消息。

        步骤开始或中断时清空缓冲区，内容部分则合并到缓冲区。

        Args:
            msg: 要处理的 Wire 消息。
        """
        match msg:
            case StepBegin() | StepInterrupted():
                self._content_buffer.clear()
            case ContentPart() as part:
                _merge_content(self._content_buffer, part)
            case _:
                pass

    def flush(self) -> None:
        """刷新缓冲区，输出最终 JSON 格式的消息。"""
        if not self._content_buffer:
            return
        message = Message(role="assistant", content=self._content_buffer)
        text = message.extract_text()
        if text:
            final_message = Message(role="assistant", content=text)
            print(final_message.model_dump_json(exclude_none=True), flush=True)
        self._content_buffer.clear()


async def visualize(output_format: OutputFormat, final_only: bool, wire: Wire) -> None:
    """可视化输出 Wire 消息流。

    根据输出格式和是否仅输出最终结果，选择合适的打印器，
    从 Wire 接收消息并格式化输出。

    Args:
        output_format: 输出格式，支持 "text" 或 "stream-json"。
        final_only: 是否仅输出最终结果。
        wire: Wire 连接实例。
    """
    if final_only:
        match output_format:
            case "text":
                handler = FinalOnlyTextPrinter()
            case "stream-json":
                handler = FinalOnlyJsonPrinter()
    else:
        match output_format:
            case "text":
                handler = TextPrinter()
            case "stream-json":
                handler = JsonPrinter()

    wire_ui = wire.ui_side(merge=True)
    while True:
        try:
            msg = await wire_ui.receive()
        except QueueShutDown:
            handler.flush()
            break

        handler.feed(msg)

        if isinstance(msg, StepInterrupted):
            break
