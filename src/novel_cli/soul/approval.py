"""审批模块。

提供工具调用的审批机制，支持手动审批、自动审批和 YOLO 模式（跳过审批）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Literal

from novel_cli.approval_runtime import (
    ApprovalCancelledError,
    ApprovalRuntime,
    ApprovalSource,
    get_current_approval_source_or_none,
)
from novel_cli.soul.toolset import get_current_tool_call_or_none
from novel_cli.tools.utils import ToolRejectedError
from novel_cli.utils.logging import logger
from novel_cli.wire.types import DisplayBlock

type Response = Literal["approve", "approve_for_session", "reject"]


class ApprovalResult:
    """审批请求的结果。为向后兼容而表现为布尔值。

    Args:
        approved: 是否通过审批。
        feedback: 用户反馈信息，可选。

    Attributes:
        approved: 是否通过审批。
        feedback: 用户反馈信息。
    """

    __slots__ = ("approved", "feedback")

    def __init__(self, approved: bool, feedback: str = ""):
        self.approved = approved
        self.feedback = feedback

    def __bool__(self) -> bool:
        return self.approved

    def rejection_error(self) -> ToolRejectedError:
        """生成拒绝错误的异常对象。

        Returns:
            包含拒绝信息的 ToolRejectedError 异常。
        """
        if self.feedback:
            return ToolRejectedError(
                message=(f"The tool call is rejected by the user. User feedback: {self.feedback}"),
                brief=f"Rejected: {self.feedback}",
                has_feedback=True,
            )
        source = get_current_approval_source_or_none()
        is_subagent = source is not None and source.agent_id is not None
        if is_subagent:
            return ToolRejectedError(
                message=(
                    "The tool call is rejected by the user. "
                    "Try a different approach to complete your task, or explain the "
                    "limitation in your summary if no alternative is available. "
                    "Do not retry the same tool call, and do not attempt to bypass "
                    "this restriction through indirect means."
                ),
            )
        return ToolRejectedError()


class ApprovalState:
    """审批状态，管理 YOLO 模式和自动审批配置。

    Args:
        yolo: 是否启用 YOLO 模式（跳过所有审批）。
        auto_approve_actions: 自动审批的操作名称集合。
        on_change: 状态变更回调函数，可选。

    Attributes:
        yolo: 是否启用 YOLO 模式。
        auto_approve_actions: 自动审批的操作名称集合。
    """

    def __init__(
        self,
        yolo: bool = False,
        auto_approve_actions: set[str] | None = None,
        on_change: Callable[[], None] | None = None,
    ):
        self.yolo = yolo
        self.auto_approve_actions: set[str] = auto_approve_actions or set()
        """应自动审批的操作名称集合。"""
        self._on_change = on_change

    def notify_change(self) -> None:
        """通知状态变更。"""
        if self._on_change is not None:
            self._on_change()


class Approval:
    """审批管理器，处理工具调用的审批请求。

    支持三种审批模式：
    - 正常模式：每个操作都需要用户审批
    - 会话自动审批：特定操作在会话期间自动审批
    - YOLO 模式：跳过所有审批

    Args:
        yolo: 是否启用 YOLO 模式。
        state: 共享的审批状态，可选。
        runtime: 审批运行时，可选。

    Attributes:
        _state: 审批状态实例。
        _runtime: 审批运行时实例。
    """

    def __init__(
        self,
        yolo: bool = False,
        *,
        state: ApprovalState | None = None,
        runtime: ApprovalRuntime | None = None,
    ):
        self._state = state or ApprovalState(yolo=yolo)
        self._runtime = runtime or ApprovalRuntime()

    def share(self) -> Approval:
        """创建共享状态（yolo + auto-approve）的新审批队列。

        Returns:
            共享状态的新 Approval 实例。
        """
        return Approval(state=self._state, runtime=self._runtime)

    def set_runtime(self, runtime: ApprovalRuntime) -> None:
        """设置审批运行时。

        Args:
            runtime: 审批运行时实例。
        """
        self._runtime = runtime

    @property
    def runtime(self) -> ApprovalRuntime:
        """获取当前审批运行时。

        Returns:
            当前的审批运行时实例。
        """
        return self._runtime

    def set_yolo(self, yolo: bool) -> None:
        """设置 YOLO 模式状态。

        Args:
            yolo: 是否启用 YOLO 模式。
        """
        self._state.yolo = yolo
        self._state.notify_change()

    def is_yolo(self) -> bool:
        """检查是否处于 YOLO 模式。

        Returns:
            如果处于 YOLO 模式则返回 True，否则返回 False。
        """
        return self._state.yolo

    async def request(
        self,
        sender: str,
        action: str,
        description: str,
        display: list[DisplayBlock] | None = None,
    ) -> ApprovalResult:
        """请求对指定操作的审批。由工具调用。

        Args:
            sender: 发送者名称。
            action: 请求审批的操作名称，用于自动审批识别。
            description: 操作描述，用于向用户展示。
            display: 显示块列表，可选。

        Returns:
            审批结果，包含 ``approved`` 标志和可选的 ``feedback``。
            通过 ``__bool__`` 可作为布尔值使用，因此 ``if not result:`` 有效。

        Raises:
            RuntimeError: 如果在工具调用之外请求审批。
        """
        tool_call = get_current_tool_call_or_none()
        if tool_call is None:
            raise RuntimeError("Approval must be requested from a tool call.")

        logger.debug(
            "{tool_name} ({tool_call_id}) requesting approval: {action} {description}",
            tool_name=tool_call.function.name,
            tool_call_id=tool_call.id,
            action=action,
            description=description,
        )
        if self._state.yolo:
            return ApprovalResult(approved=True)

        if action in self._state.auto_approve_actions:
            return ApprovalResult(approved=True)

        request_id = str(uuid.uuid4())
        display_blocks = display or []
        source = get_current_approval_source_or_none() or ApprovalSource(
            kind="foreground_turn",
            id=tool_call.id,
        )
        self._runtime.create_request(
            request_id=request_id,
            tool_call_id=tool_call.id,
            sender=sender,
            action=action,
            description=description,
            display=display_blocks,
            source=source,
        )
        try:
            response, feedback = await self._runtime.wait_for_response(request_id)
        except ApprovalCancelledError:
            return ApprovalResult(approved=False)
        match response:
            case "approve":
                return ApprovalResult(approved=True)
            case "approve_for_session":
                self._state.auto_approve_actions.add(action)
                self._state.notify_change()
                for pending in self._runtime.list_pending():
                    if pending.action == action:
                        self._runtime.resolve(pending.id, "approve")
                return ApprovalResult(approved=True)
            case "reject":
                return ApprovalResult(approved=False, feedback=feedback)