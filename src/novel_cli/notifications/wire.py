"""通知 Wire 协议转换模块。

将内部通知视图转换为 Wire 协议格式。
"""

from __future__ import annotations

from novel_cli.wire.types import Notification

from .models import NotificationView


def to_wire_notification(view: NotificationView) -> Notification:
    """将 NotificationView 转换为 Wire 协议的 Notification 对象。

    Args:
        view: 内部通知视图对象。

    Returns:
        Wire 协议格式的 Notification 对象。
    """
    event = view.event
    return Notification(
        id=event.id,
        category=event.category,
        type=event.type,
        source_kind=event.source_kind,
        source_id=event.source_id,
        title=event.title,
        body=event.body,
        severity=event.severity,
        created_at=event.created_at,
        payload=event.payload,
    )
