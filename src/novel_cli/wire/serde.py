"""Wire 消息序列化/反序列化模块。

本模块提供 WireMessage 与 JSON 可序列化字典之间的转换功能。
"""

from __future__ import annotations

from typing import Any

from kosong.utils.typing import JsonType

from novel_cli.wire.types import WireMessage, WireMessageEnvelope


def serialize_wire_message(msg: WireMessage) -> dict[str, JsonType]:
    """将 WireMessage 转换为 JSON 可序列化的字典。

    Args:
        msg: 要序列化的 Wire 消息。

    Returns:
        可转换为 JSON 的字典表示。
    """
    envelope = WireMessageEnvelope.from_wire_message(msg)
    return envelope.model_dump(mode="json")


def deserialize_wire_message(data: dict[str, JsonType] | Any) -> WireMessage:
    """将 JSON 可序列化的字典转换为 WireMessage。

    Args:
        data: JSON 可序列化的字典数据。

    Returns:
        转换后的 Wire 消息对象。

    Raises:
        ValueError: 如果消息类型未知或 payload 无效。
    """
    envelope = WireMessageEnvelope.model_validate(data)
    return envelope.to_wire_message()
