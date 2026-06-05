"""审批运行时核心模块。

提供审批请求的创建、等待、解决和取消功能。
"""

from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from novel_cli.utils.logging import logger
from novel_cli.wire.types import ApprovalRequest, ApprovalResponse

from .models import (
    ApprovalRequestRecord,
    ApprovalResponseKind,
    ApprovalRuntimeEvent,
    ApprovalSource,
    ApprovalSourceKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from novel_cli.wire.root_hub import RootWireHub
    from novel_cli.wire.types import DisplayBlock


class ApprovalCancelledError(Exception):
    """审批取消异常，当待处理的审批被其来源生命周期取消时抛出。"""


# 当前审批来源上下文变量
_current_approval_source = ContextVar[ApprovalSource | None](
    "current_approval_source",
    default=None,
)


def get_current_approval_source_or_none() -> ApprovalSource | None:
    """获取当前审批来源，未设置时返回 None。

    Returns:
        当前审批来源对象，未设置返回 None。
    """
    return _current_approval_source.get()


def set_current_approval_source(source: ApprovalSource) -> Token[ApprovalSource | None]:
    """设置当前审批来源。

    Args:
        source: 审批来源对象。

    Returns:
        上下文变量 Token，用于后续恢复。
    """
    return _current_approval_source.set(source)


def reset_current_approval_source(token: Token[ApprovalSource | None]) -> None:
    """恢复审批来源上下文变量。

    Args:
        token: 之前保存的上下文变量 Token。
    """
    _current_approval_source.reset(token)


class ApprovalRuntime:
    """审批运行时，管理审批请求的生命周期。

    负责审批请求的创建、等待响应、解决和取消。

    Attributes:
        _requests: 请求记录字典。
        _waiters: 等待器字典。
        _subscribers: 事件订阅者字典。
        _root_wire_hub: Wire Hub 绑定，可选。
    """

    def __init__(self) -> None:
        """初始化审批运行时。"""
        self._requests: dict[str, ApprovalRequestRecord] = {}
        self._waiters: dict[str, asyncio.Future[tuple[ApprovalResponseKind, str]]] = {}
        self._subscribers: dict[str, Callable[[ApprovalRuntimeEvent], None]] = {}
        self._root_wire_hub: RootWireHub | None = None

    def bind_root_wire_hub(self, root_wire_hub: RootWireHub) -> None:
        """绑定 Wire Hub，用于发布审批事件。

        Args:
            root_wire_hub: Root Wire Hub 对象。
        """
        if self._root_wire_hub is root_wire_hub:
            return
        self._root_wire_hub = root_wire_hub

    def create_request(
        self,
        *,
        sender: str,
        action: str,
        description: str,
        tool_call_id: str,
        display: list[DisplayBlock],
        source: ApprovalSource,
        request_id: str | None = None,
    ) -> ApprovalRequestRecord:
        """创建审批请求。

        Args:
            sender: 发送者名称。
            action: 动作描述。
            description: 详细描述。
            tool_call_id: 工具调用标识符。
            display: 显示块列表。
            source: 请求来源。
            request_id: 请求 ID，可选，未提供时自动生成。

        Returns:
            创建的审批请求记录。
        """
        request = ApprovalRequestRecord(
            id=request_id or str(uuid.uuid4()),
            tool_call_id=tool_call_id,
            sender=sender,
            action=action,
            description=description,
            display=display,
            source=source,
        )
        self._requests[request.id] = request
        self._publish_event(ApprovalRuntimeEvent(kind="request_created", request=request))
        self._publish_wire_request(request)
        return request

    async def wait_for_response(
        self, request_id: str, timeout: float = 300.0
    ) -> tuple[ApprovalResponseKind, str]:
        """等待审批响应。

        Args:
            request_id: 请求 ID。
            timeout: 超时时间（秒），默认 300 秒。

        Returns:
            响应类型和反馈文本的元组。

        Raises:
            KeyError: 请求不存在。
            ApprovalCancelledError: 请求被取消或超时。
        """
        waiter = self._waiters.get(request_id)
        request = self._requests.get(request_id)
        if request is None:
            raise KeyError(f"Approval request not found: {request_id}")
        if waiter is None:
            if request.status == "cancelled":
                raise ApprovalCancelledError(request_id)
            if request.status == "resolved":
                assert request.response is not None
                return request.response, request.feedback
            waiter = asyncio.get_running_loop().create_future()
            self._waiters[request_id] = waiter
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "Approval request {id} timed out after {t}s",
                id=request_id,
                t=timeout,
            )
            # 在取消前弹出等待器，避免 _cancel_request 对无人等待的 future 设置异常
            # 这会触发 asyncio 的 "exception was never retrieved" 警告
            self._waiters.pop(request_id, None)
            self._cancel_request(request_id, feedback="approval timed out")
            raise ApprovalCancelledError(request_id) from None

    def resolve(self, request_id: str, response: ApprovalResponseKind, feedback: str = "") -> bool:
        """解决审批请求。

        Args:
            request_id: 请求 ID。
            response: 响应类型。
            feedback: 反馈文本。

        Returns:
            如果成功解决返回 True，请求不存在或已解决返回 False。
        """
        request = self._requests.get(request_id)
        if request is None or request.status != "pending":
            return False
        request.status = "resolved"
        request.response = response
        request.feedback = feedback
        import time

        request.resolved_at = time.time()
        waiter = self._waiters.pop(request_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result((response, feedback))
        self._publish_event(ApprovalRuntimeEvent(kind="request_resolved", request=request))
        self._publish_wire_response(request_id, response, feedback)
        return True

    def _cancel_request(self, request_id: str, feedback: str = "") -> None:
        """取消单个待处理请求。

        Args:
            request_id: 请求 ID。
            feedback: 反馈文本。
        """
        import time

        request = self._requests.get(request_id)
        if request is None or request.status != "pending":
            return
        request.status = "cancelled"
        request.response = "reject"
        request.feedback = feedback
        request.resolved_at = time.time()
        waiter = self._waiters.pop(request_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_exception(ApprovalCancelledError(request_id))
        self._publish_event(ApprovalRuntimeEvent(kind="request_resolved", request=request))
        self._publish_wire_response(request_id, "reject", feedback)

    def cancel_by_source(self, source_kind: ApprovalSourceKind, source_id: str) -> int:
        """按来源取消所有待处理请求。

        Args:
            source_kind: 来源类型。
            source_id: 来源标识符。

        Returns:
            取消的请求数量。
        """
        cancelled = 0
        import time

        for request_id, request in self._requests.items():
            if request.status != "pending":
                continue
            if request.source.kind != source_kind or request.source.id != source_id:
                continue
            request.status = "cancelled"
            request.response = "reject"
            request.resolved_at = time.time()
            waiter = self._waiters.pop(request_id, None)
            if waiter is not None and not waiter.done():
                waiter.set_exception(ApprovalCancelledError(request_id))
            self._publish_event(ApprovalRuntimeEvent(kind="request_resolved", request=request))
            self._publish_wire_response(request_id, "reject")
            cancelled += 1
        return cancelled

    def list_pending(self) -> list[ApprovalRequestRecord]:
        """列出所有待处理请求，按创建时间排序。

        Returns:
        待处理请求列表。
        """
        pending = [request for request in self._requests.values() if request.status == "pending"]
        pending.sort(key=lambda request: request.created_at)
        return pending

    def get_request(self, request_id: str) -> ApprovalRequestRecord | None:
        """获取请求记录。

        Args:
            request_id: 请求 ID。

        Returns:
            请求记录，未找到返回 None。
        """
        return self._requests.get(request_id)

    def subscribe(self, callback: Callable[[ApprovalRuntimeEvent], None]) -> str:
        """订阅审批运行时事件。

        Args:
            callback: 事件回调函数。

        Returns:
            订阅 Token，用于取消订阅。
        """
        token = uuid.uuid4().hex
        self._subscribers[token] = callback
        return token

    def unsubscribe(self, token: str) -> None:
        """取消订阅。

        Args:
            token: 订阅 Token。
        """
        self._subscribers.pop(token, None)

    def _publish_event(self, event: ApprovalRuntimeEvent) -> None:
        """发布运行时事件到所有订阅者。

        Args:
            event: 运行时事件对象。
        """
        for callback in list(self._subscribers.values()):
            try:
                callback(event)
            except Exception:
                logger.exception("Approval runtime event subscriber failed")

    def _publish_wire_request(self, request: ApprovalRequestRecord) -> None:
        """通过 Wire Hub 发布审批请求。

        Args:
            request: 审批请求记录。
        """
        if self._root_wire_hub is None:
            return
        self._root_wire_hub.publish_nowait(
            ApprovalRequest(
                id=request.id,
                tool_call_id=request.tool_call_id,
                sender=request.sender,
                action=request.action,
                description=request.description,
                display=request.display,
                source_kind=request.source.kind,
                source_id=request.source.id,
                agent_id=request.source.agent_id,
                subagent_type=request.source.subagent_type,
            )
        )

    def _publish_wire_response(
        self, request_id: str, response: ApprovalResponseKind, feedback: str = ""
    ) -> None:
        """通过 Wire Hub 发布审批响应。

        Args:
            request_id: 请求 ID。
            response: 响应类型。
            feedback: 反馈文本。
        """
        if self._root_wire_hub is None:
            return
        self._root_wire_hub.publish_nowait(
            ApprovalResponse(
                request_id=request_id,
                response=response,
                feedback=feedback,
            )
        )
