"""通知持久化存储模块。

提供通知事件和投递状态的文件系统存储功能。
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import ValidationError

from novel_cli.utils.io import atomic_json_write
from novel_cli.utils.logging import logger

from .models import NotificationDelivery, NotificationEvent, NotificationView

# 有效的通知 ID 正则表达式
_VALID_NOTIFICATION_ID = re.compile(r"^[a-z0-9]{2,20}$")


def _validate_notification_id(notification_id: str) -> None:
    """验证通知 ID 格式。

    Args:
        notification_id: 待验证的通知 ID。

    Raises:
        ValueError: 如果通知 ID 格式无效。
    """
    if not _VALID_NOTIFICATION_ID.match(notification_id):
        raise ValueError(f"Invalid notification_id: {notification_id!r}")


class NotificationStore:
    """通知存储类，负责通知的持久化读写。

    使用文件系统存储通知事件和投递状态。

    Attributes:
        EVENT_FILE: 事件文件名。
        DELIVERY_FILE: 投递状态文件名。
    """

    EVENT_FILE = "event.json"
    DELIVERY_FILE = "delivery.json"

    def __init__(self, root: Path):
        """初始化通知存储。

        Args:
            root: 存储根目录路径。
        """
        self._root = root

    @property
    def root(self) -> Path:
        """返回存储根目录路径。"""
        return self._root

    def _ensure_root(self) -> Path:
        """确保根目录存在，不存在则创建。

        Returns:
            根目录路径。
        """
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def notification_dir(self, notification_id: str) -> Path:
        """获取通知目录路径，确保目录存在。

        Args:
            notification_id: 通知 ID。

        Returns:
            通知目录路径。
        """
        _validate_notification_id(notification_id)
        path = self._ensure_root() / notification_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def notification_path(self, notification_id: str) -> Path:
        """获取通知目录路径（不创建）。

        Args:
            notification_id: 通知 ID。

        Returns:
            通知目录路径。
        """
        _validate_notification_id(notification_id)
        return self.root / notification_id

    def event_path(self, notification_id: str) -> Path:
        """获取事件文件路径。

        Args:
            notification_id: 通知 ID。

        Returns:
            事件文件路径。
        """
        return self.notification_path(notification_id) / self.EVENT_FILE

    def delivery_path(self, notification_id: str) -> Path:
        """获取投递状态文件路径。

        Args:
            notification_id: 通知 ID。

        Returns:
            投递状态文件路径。
        """
        return self.notification_path(notification_id) / self.DELIVERY_FILE

    def create_notification(
        self,
        event: NotificationEvent,
        delivery: NotificationDelivery,
    ) -> None:
        """创建新通知，写入事件和投递状态文件。

        Args:
            event: 通知事件。
            delivery: 投递状态。
        """
        notification_dir = self.notification_dir(event.id)
        atomic_json_write(event.model_dump(mode="json"), notification_dir / self.EVENT_FILE)
        atomic_json_write(delivery.model_dump(mode="json"), notification_dir / self.DELIVERY_FILE)

    def list_notification_ids(self) -> list[str]:
        """列出所有通知 ID。

        Returns:
            通知 ID 列表，按名称排序。
        """
        if not self.root.exists():
            return []
        notification_ids: list[str] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir():
                continue
            if not (path / self.EVENT_FILE).exists():
                continue
            notification_ids.append(path.name)
        return notification_ids

    def read_event(self, notification_id: str) -> NotificationEvent:
        """读取通知事件。

        Args:
            notification_id: 通知 ID。

        Returns:
            通知事件对象。
        """
        return NotificationEvent.model_validate_json(
            self.event_path(notification_id).read_text(encoding="utf-8")
        )

    def write_event(self, event: NotificationEvent) -> None:
        """写入通知事件。

        Args:
            event: 通知事件对象。
        """
        atomic_json_write(event.model_dump(mode="json"), self.event_path(event.id))

    def read_delivery(self, notification_id: str) -> NotificationDelivery:
        """读取投递状态，失败时返回默认值。

        Args:
            notification_id: 通知 ID。

        Returns:
            投递状态对象。
        """
        path = self.delivery_path(notification_id)
        if not path.exists():
            return NotificationDelivery()
        try:
            return NotificationDelivery.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError, UnicodeDecodeError) as exc:
            logger.warning(
                "Failed to read notification delivery {path}; using defaults: {error}",
                path=path,
                error=exc,
            )
            return NotificationDelivery()

    def write_delivery(self, notification_id: str, delivery: NotificationDelivery) -> None:
        """写入投递状态。

        Args:
            notification_id: 通知 ID。
            delivery: 投递状态对象。
        """
        atomic_json_write(delivery.model_dump(mode="json"), self.delivery_path(notification_id))

    def merged_view(self, notification_id: str) -> NotificationView:
        """合并事件和投递状态为视图。

        Args:
            notification_id: 通知 ID。

        Returns:
            通知视图对象。
        """
        return NotificationView(
            event=self.read_event(notification_id),
            delivery=self.read_delivery(notification_id),
        )

    def list_views(self) -> list[NotificationView]:
        """列出所有通知视图，按创建时间倒序排列。

        Returns:
            通知视图列表。
        """
        views: list[NotificationView] = []
        for notification_id in self.list_notification_ids():
            try:
                views.append(self.merged_view(notification_id))
            except (OSError, ValidationError, ValueError, UnicodeDecodeError) as exc:
                logger.warning(
                    "Skipping invalid notification {notification_id} from {path}: {error}",
                    notification_id=notification_id,
                    path=self.root / notification_id / self.EVENT_FILE,
                    error=exc,
                )
        views.sort(key=lambda view: view.event.created_at, reverse=True)
        return views
