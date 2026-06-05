"""消息字符串化工具。

本模块提供将 Message 对象转换为可读字符串的功能，
用于日志和显示消息内容。
"""

from __future__ import annotations

from kosong.message import Message

from novel_cli.wire.types import AudioURLPart, ImageURLPart, TextPart, VideoURLPart


def message_stringify(message: Message) -> str:
    """获取消息的字符串表示。

    将消息中的各种内容部分转换为可读的字符串格式，
    文本部分保留原文，媒体部分使用占位符表示。

    Args:
        message: 要字符串化的消息对象。

    Returns:
        消息的字符串表示。
    """
    # TODO: this should be merged into `kosong.message.Message.extract_text`
    parts: list[str] = []
    for part in message.content:
        if isinstance(part, TextPart):
            parts.append(part.text)
        elif isinstance(part, ImageURLPart):
            parts.append("[image]")
        elif isinstance(part, AudioURLPart):
            suffix = f":{part.audio_url.id}" if part.audio_url.id else ""
            parts.append(f"[audio{suffix}]")
        elif isinstance(part, VideoURLPart):
            parts.append("[video]")
        else:
            parts.append(f"[{part.type}]")
    return "".join(parts)