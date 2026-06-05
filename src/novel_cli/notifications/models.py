"""通知数据模型模块。

定义通知事件、投递状态和视图的数据结构。
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 通知类别类型
type NotificationCategory = Literal["task", "agent", "system"]
# 通知严重级别类型
type NotificationSeverity = Literal["info", "success", "warning", "error"]
# 通知投递目标 Sink 类型
type NotificationSink = Literal["llm", "wire", "shell"]
# 通知投递状态类型
type NotificationDeliveryStatus = Literal["pending", "claimed", "acked"]


class NotificationEvent(BaseModel):
    """通知事件模型，表示一个待投递的通知。

    Attributes:
        version: 模型版本号。
        id: 通知唯一标识符。
        category: 通知类别（task/agent/system）。
        type: 通知类型。
        source_kind: 来源类型。
        source_id: 来源标识符。
        title: 通知标题。
        body: 通知正文。
        severity: 严重级别，默认 info。
        created_at: 创建时间戳。
        payload: 附加数据。
        targets: 目标 Sink 列表。
        dedupe_key: 去重键，用于避免重复通知。
    """

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    id: str
    category: NotificationCategory
    type: str
    source_kind: str
    source_id: str
    title: str
    body: str
    severity: NotificationSeverity = "info"
    created_at: float = Field(default_factory=time.time)
    payload: dict[str, Any] = Field(default_factory=dict)
    targets: list[NotificationSink] = Field(default_factory=lambda: ["llm", "wire", "shell"])
    dedupe_key: str | None = None


class NotificationSinkState(BaseModel):
    """单个 Sink 的投递状态。

    Attributes:
        status: 投递状态（pending/claimed/acked）。
        claimed_at: 领取时间戳。
        acked_at: 确认时间戳。
    """

    model_config = ConfigDict(extra="ignore")

    status: NotificationDeliveryStatus = "pending"
    claimed_at: float | None = None
    acked_at: float | None = None


class NotificationDelivery(BaseModel):
    """通知投递状态，记录各 Sink 的投递情况。

    Attributes:
        sinks: Sink 名称到投递状态的映射。
    """

    model_config = ConfigDict(extra="ignore")

    sinks: dict[str, NotificationSinkState] = Field(default_factory=dict)


class NotificationView(BaseModel):
    """通知视图，组合事件和投递状态。

    Attributes:
        event: 通知事件。
        delivery: 投递状态。
    """

    model_config = ConfigDict(extra="ignore")

    event: NotificationEvent
    delivery: NotificationDelivery
