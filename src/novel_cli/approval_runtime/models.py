"""审批运行时数据模型模块。

定义审批请求、响应、来源和运行时事件的数据结构。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from novel_cli.wire.types import DisplayBlock

# 审批响应类型
type ApprovalResponseKind = Literal["approve", "approve_for_session", "reject"]
# 审批来源类型
type ApprovalSourceKind = Literal["foreground_turn", "background_agent"]
# 审批状态
type ApprovalStatus = Literal["pending", "resolved", "cancelled"]
# 审批运行时事件类型
type ApprovalRuntimeEventKind = Literal["request_created", "request_resolved"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalSource:
    """审批请求来源。

    Attributes:
        kind: 来源类型（foreground_turn/background_agent）。
        id: 来源标识符。
        agent_id: 代理标识符，可选。
        subagent_type: 子代理类型，可选。
    """

    kind: ApprovalSourceKind
    id: str
    agent_id: str | None = None
    subagent_type: str | None = None


@dataclass(slots=True, kw_only=True)
class ApprovalRequestRecord:
    """审批请求记录。

    Attributes:
        id: 请求唯一标识符。
        tool_call_id: 工具调用标识符。
        sender: 发送者名称。
        action: 动作描述。
        description: 详细描述。
        display: 显示块列表。
        source: 请求来源。
        created_at: 创建时间戳。
        status: 审批状态。
        resolved_at: 解决时间戳，可选。
        response: 响应类型，可选。
        feedback: 反馈文本。
    """

    id: str
    tool_call_id: str
    sender: str
    action: str
    description: str
    display: list[DisplayBlock]
    source: ApprovalSource
    created_at: float = field(default_factory=time.time)
    status: ApprovalStatus = "pending"
    resolved_at: float | None = None
    response: ApprovalResponseKind | None = None
    feedback: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalRuntimeEvent:
    """审批运行时事件。

    Attributes:
        kind: 事件类型（request_created/request_resolved）。
        request: 相关的审批请求记录。
    """

    kind: ApprovalRuntimeEventKind
    request: ApprovalRequestRecord
