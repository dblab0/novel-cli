"""wire.jsonl 精简格式生成。

只保留核心消息类型：metadata / TurnBegin / TurnEnd / ToolCall / ToolResult / ContentPart。
"""

from __future__ import annotations

import time


def _ts() -> float:
    return time.time()


def make_metadata() -> dict:
    """生成协议头部。"""
    return {"type": "metadata", "protocol_version": "1.8"}


def make_turn_begin(user_input: str, original_input: str | None = None) -> dict:
    """生成用户输入消息。

    Args:
        user_input: 实际写入 wire 的用户输入（skill 展开后的文本）。
        original_input: 原始用户输入（如 /skill:xxx），用于前端展示还原。
    """
    payload: dict = {"user_input": user_input}
    if original_input is not None:
        payload["original_input"] = original_input
    return {
        "timestamp": _ts(),
        "message": {"type": "TurnBegin", "payload": payload},
    }


def make_turn_end() -> dict:
    """生成轮次结束消息。"""
    return {"timestamp": _ts(), "message": {"type": "TurnEnd", "payload": {}}}


def make_tool_call(tc_id: str, name: str, arguments: str) -> dict:
    """生成工具调用消息。"""
    return {
        "timestamp": _ts(),
        "message": {
            "type": "ToolCall",
            "payload": {
                "type": "function",
                "id": tc_id,
                "function": {"name": name, "arguments": arguments},
                "extras": None,
            },
        },
    }


def make_tool_result(tc_id: str, return_value: dict) -> dict:
    """生成工具结果消息。"""
    return {
        "timestamp": _ts(),
        "message": {
            "type": "ToolResult",
            "payload": {"tool_call_id": tc_id, "return_value": return_value},
        },
    }


def make_text_part(text: str) -> dict:
    """生成文本消息（用户粘贴的模型回答）。"""
    return {
        "timestamp": _ts(),
        "message": {"type": "ContentPart", "payload": {"type": "text", "text": text}},
    }
