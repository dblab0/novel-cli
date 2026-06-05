"""消息处理模块。

提供消息构建、转换和检查的辅助函数，用于处理 LLM 对话中的各种消息格式。
"""

from __future__ import annotations

from collections.abc import Sequence

from kosong.message import Message
from kosong.tooling.error import ToolRuntimeError

from novel_cli.llm import ModelCapability
from novel_cli.wire.types import (
    ContentPart,
    ImageURLPart,
    TextPart,
    ThinkPart,
    ToolResult,
    VideoURLPart,
)


def system(message: str) -> ContentPart:
    """创建系统消息内容部分。

    Args:
        message: 系统消息文本。

    Returns:
        包装在 system 标签中的文本内容部分。
    """
    return TextPart(text=f"<system>{message}</system>")


def system_reminder(message: str) -> TextPart:
    """创建系统提醒消息。

    Args:
        message: 提醒消息文本。

    Returns:
        包装在 system-reminder 标签中的文本部分。
    """
    return TextPart(text=f"<system-reminder>\n{message}\n</system-reminder>")


def is_system_reminder_message(message: Message) -> bool:
    """检查消息是否为内部 system-reminder 用户消息。

    Args:
        message: 待检查的消息。

    Returns:
        如果消息是系统提醒消息则返回 True，否则返回 False。
    """
    if message.role != "user" or len(message.content) != 1:
        return False
    part = message.content[0]
    return isinstance(part, TextPart) and part.text.strip().startswith("<system-reminder>")


def tool_result_to_message(tool_result: ToolResult) -> Message:
    """将工具结果转换为消息。

    Args:
        tool_result: 工具执行结果。

    Returns:
        转换后的消息对象。
    """
    if tool_result.return_value.is_error:
        assert tool_result.return_value.message, "Error return value should have a message"
        message = tool_result.return_value.message
        if isinstance(tool_result.return_value, ToolRuntimeError):
            message += "\nThis is an unexpected error and the tool is probably not working."
        content: list[ContentPart] = [system(f"ERROR: {message}")]
        if tool_result.return_value.output:
            content.extend(_output_to_content_parts(tool_result.return_value.output))
    else:
        content: list[ContentPart] = []
        if tool_result.return_value.message:
            content.append(system(tool_result.return_value.message))
        if tool_result.return_value.output:
            content.extend(_output_to_content_parts(tool_result.return_value.output))
        if not content:
            content.append(system("Tool output is empty."))
        elif not any(isinstance(part, TextPart) for part in content):
            # 确保至少存在一个 TextPart，以免 LLM API 以 "text content is empty" 拒绝消息（参见 #1663）。
            content.insert(0, system("Tool returned non-text content."))

    return Message(
        role="tool",
        content=content,
        tool_call_id=tool_result.tool_call_id,
    )


def _output_to_content_parts(
    output: str | ContentPart | Sequence[ContentPart],
) -> list[ContentPart]:
    """将输出转换为内容部分列表。

    Args:
        output: 输出内容，可以是字符串、单个内容部分或内容部分序列。

    Returns:
        内容部分列表。
    """
    content: list[ContentPart] = []
    match output:
        case str(text):
            if text:
                content.append(TextPart(text=text))
        case ContentPart():
            content.append(output)
        case _:
            content.extend(output)
    return content


def check_message(
    message: Message, model_capabilities: set[ModelCapability]
) -> set[ModelCapability]:
    """检查消息内容，返回缺失的模型能力。

    Args:
        message: 待检查的消息。
        model_capabilities: 模型已具备的能力集合。

    Returns:
        消息需要但模型缺失的能力集合。
    """
    capabilities_needed = set[ModelCapability]()
    for part in message.content:
        if isinstance(part, ImageURLPart):
            capabilities_needed.add("image_in")
        elif isinstance(part, VideoURLPart):
            capabilities_needed.add("video_in")
        elif isinstance(part, ThinkPart):
            capabilities_needed.add("thinking")
    return capabilities_needed - model_capabilities