"""通知系统模块。

提供通知事件的发布、存储和投递功能，支持多 Sink（LLM、Wire、Shell）投递。
"""

from .llm import build_notification_message, extract_notification_ids, is_notification_message
from .manager import NotificationManager
from .models import (
    NotificationCategory,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEvent,
    NotificationSeverity,
    NotificationSink,
    NotificationSinkState,
    NotificationView,
)
from .notifier import NotificationWatcher
from .store import NotificationStore
from .wire import to_wire_notification

__all__ = [
    "NotificationCategory",
    "NotificationDelivery",
    "NotificationDeliveryStatus",
    "NotificationEvent",
    "NotificationManager",
    "NotificationSeverity",
    "NotificationSink",
    "NotificationSinkState",
    "NotificationStore",
    "NotificationView",
    "NotificationWatcher",
    "build_notification_message",
    "extract_notification_ids",
    "is_notification_message",
    "to_wire_notification",
]
