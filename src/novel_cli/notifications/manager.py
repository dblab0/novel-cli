"""通知管理器模块。

提供通知的发布、领取、确认和投递协调功能。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from novel_cli.config import NotificationConfig
from novel_cli.utils.logging import logger

from .models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationSinkState,
    NotificationView,
)
from .store import NotificationStore


class NotificationManager:
    """通知管理器，协调通知的发布和投递。

    负责通知的生命周期管理，包括发布、领取、确认和恢复过期领取。

    Attributes:
        _config: 通知配置。
        _store: 通知存储实例。
    """

    def __init__(self, root: Path, config: NotificationConfig) -> None:
        """初始化通知管理器。

        Args:
            root: 存储根目录路径。
            config: 通知配置。
        """
        self._config = config
        self._store = NotificationStore(root)

    @property
    def store(self) -> NotificationStore:
        """返回通知存储实例。"""
        return self._store

    def new_id(self) -> str:
        """生成新的通知 ID。"""
        return f"n{uuid.uuid4().hex[:8]}"

    def _initial_delivery(self, event: NotificationEvent) -> NotificationDelivery:
        """创建初始投递状态。

        Args:
            event: 通知事件。

        Returns:
            初始投递状态对象。
        """
        return NotificationDelivery(sinks={sink: NotificationSinkState() for sink in event.targets})

    def find_by_dedupe_key(self, dedupe_key: str) -> NotificationView | None:
        """通过去重键查找通知。

        Args:
            dedupe_key: 去重键。

        Returns:
            匹配的通知视图，未找到返回 None。
        """
        for view in self._store.list_views():
            if view.event.dedupe_key == dedupe_key:
                return view
        return None

    def publish(self, event: NotificationEvent) -> NotificationView:
        """发布新通知。

        如果事件有去重键且已存在相同去重键的通知，则返回已存在的通知。

        Args:
            event: 通知事件。

        Returns:
            通知视图。
        """
        if event.dedupe_key:
            existing = self.find_by_dedupe_key(event.dedupe_key)
            if existing is not None:
                return existing
        delivery = self._initial_delivery(event)
        self._store.create_notification(event, delivery)
        return NotificationView(event=event, delivery=delivery)

    def recover(self) -> None:
        """恢复过期的领取状态。

        将超过过期时间的 claimed 状态重置为 pending。
        """
        now = time.time()
        stale_after = self._config.claim_stale_after_ms / 1000
        for view in self._store.list_views():
            updated = False
            delivery = view.delivery.model_copy(deep=True)
            for sink_state in delivery.sinks.values():
                if sink_state.status != "claimed" or sink_state.claimed_at is None:
                    continue
                if now - sink_state.claimed_at <= stale_after:
                    continue
                sink_state.status = "pending"
                sink_state.claimed_at = None
                updated = True
            if updated:
                self._store.write_delivery(view.event.id, delivery)

    def has_pending_for_sink(self, sink: str) -> bool:
        """检查是否有待投递的通知。

        Args:
            sink: Sink 名称。

        Returns:
            如果有待投递通知返回 True。
        """
        for view in self._store.list_views():
            sink_state = view.delivery.sinks.get(sink)
            if sink_state is not None and sink_state.status == "pending":
                return True
        return False

    def claim_for_sink(self, sink: str, *, limit: int = 8) -> list[NotificationView]:
        """为指定 Sink 领取待投递通知。

        Args:
            sink: Sink 名称。
            limit: 最大领取数量。

        Returns:
            已领取的通知视图列表。
        """
        self.recover()
        claimed: list[NotificationView] = []
        now = time.time()
        for view in reversed(self._store.list_views()):
            sink_state = view.delivery.sinks.get(sink)
            if sink_state is None or sink_state.status == "acked":
                continue
            if sink_state.status == "claimed":
                continue
            delivery = view.delivery.model_copy(deep=True)
            target_state = delivery.sinks[sink]
            target_state.status = "claimed"
            target_state.claimed_at = now
            self._store.write_delivery(view.event.id, delivery)
            claimed.append(NotificationView(event=view.event, delivery=delivery))
            if len(claimed) >= limit:
                break
        return claimed

    async def deliver_pending(
        self,
        sink: str,
        *,
        on_notification: Callable[[NotificationView], Awaitable[None] | None],
        limit: int = 8,
        before_claim: Callable[[], object] | None = None,
    ) -> list[NotificationView]:
        """投递待处理通知，使用共享的领取/确认流程。

        如果处理器对某个通知抛出异常，错误会被记录，该通知保持 claimed 状态
        （稍后会被恢复），投递继续处理剩余通知。

        Args:
            sink: Sink 名称。
            on_notification: 通知处理回调。
            limit: 最大投递数量。
            before_claim: 领取前的回调函数。

        Returns:
            已成功投递的通知视图列表。
        """
        if before_claim is not None:
            before_claim()

        delivered: list[NotificationView] = []
        for view in self.claim_for_sink(sink, limit=limit):
            try:
                result = on_notification(view)
                if result is not None:
                    await result
            except Exception:
                logger.exception(
                    "Notification handler failed for {sink}/{id}, leaving claimed for recovery",
                    sink=sink,
                    id=view.event.id,
                )
                continue
            delivered.append(self.ack(sink, view.event.id))
        return delivered

    def ack(self, sink: str, notification_id: str) -> NotificationView:
        """确认通知已投递。

        Args:
            sink: Sink 名称。
            notification_id: 通知 ID。

        Returns:
            更新后的通知视图。
        """
        view = self._store.merged_view(notification_id)
        delivery = view.delivery.model_copy(deep=True)
        sink_state = delivery.sinks.get(sink)
        if sink_state is None:
            return view
        sink_state.status = "acked"
        sink_state.acked_at = time.time()
        sink_state.claimed_at = None
        self._store.write_delivery(notification_id, delivery)
        return NotificationView(event=view.event, delivery=delivery)

    def ack_ids(self, sink: str, notification_ids: set[str]) -> None:
        """批量确认通知已投递。

        Args:
            sink: Sink 名称。
            notification_ids: 通知 ID 集合。
        """
        for notification_id in notification_ids:
            try:
                self.ack(sink, notification_id)
            except (FileNotFoundError, ValueError):
                continue
