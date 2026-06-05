"""Novel CLI Web 界面的 JSON-RPC 消息辅助模块。

提供会话状态更新和历史回放完成等消息的创建和发送功能。
"""

from typing import Literal
from uuid import uuid4

from fastapi import WebSocket
from pydantic import BaseModel, ConfigDict
from starlette.websockets import WebSocketState

from novel_cli.web.models import SessionStatus


class _MessageBase(BaseModel):
    """JSON-RPC 消息基类。

    Attributes:
        jsonrpc: JSON-RPC 协议版本，固定为 "2.0"。
    """

    jsonrpc: Literal["2.0"] = "2.0"
    model_config = ConfigDict(extra="forbid")


class JSONRPCSessionStatusMessage(_MessageBase):
    """会话状态更新消息。

    Attributes:
        method: 方法名，固定为 "session_status"。
        params: 会话状态对象。
    """

    method: Literal["session_status"] = "session_status"
    params: SessionStatus


class JSONRPCHistoryCompleteMessage(_MessageBase):
    """历史回放完成消息。

    在历史记录回放完成、环境准备就绪前发送。

    Attributes:
        method: 方法名，固定为 "history_complete"。
        id: 消息唯一标识符。
    """

    method: Literal["history_complete"] = "history_complete"
    id: str


def new_session_status_message(status: SessionStatus) -> JSONRPCSessionStatusMessage:
    """创建新的会话状态消息。

    Args:
        status: 会话状态对象。

    Returns:
        创建的 JSON-RPC 会话状态消息。
    """
    return JSONRPCSessionStatusMessage(params=status)


def new_history_complete_message() -> JSONRPCHistoryCompleteMessage:
    """创建新的历史回放完成消息。

    Returns:
        创建的 JSON-RPC 历史完成消息。
    """
    return JSONRPCHistoryCompleteMessage(id=str(uuid4()))


async def send_history_complete(ws: WebSocket) -> bool:
    """向 WebSocket 发送历史回放完成消息。

    Args:
        ws: WebSocket 连接对象。

    Returns:
        发送成功返回 True，连接断开或发送失败返回 False。
    """
    if ws.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await ws.send_text(new_history_complete_message().model_dump_json())
        return True
    except Exception:
        return False
