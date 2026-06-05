"""ExitPlanMode 工具 —— 让 LLM 提交计划供用户审批。

在计划模式下完成方案设计后，通过此工具提交计划文件供用户审批，
审批通过后退出计划模式，可以开始执行计划。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import override
from uuid import uuid4

from kosong.tooling import BriefDisplayBlock, CallableTool2, ToolError, ToolReturnValue
from pydantic import BaseModel, Field, field_validator

from novel_cli.soul import get_wire_or_none, wire_send
from novel_cli.soul.toolset import get_current_tool_call_or_none
from novel_cli.tools.utils import ToolRejectedError, load_desc
from novel_cli.wire.types import (
    PlanDisplay,
    QuestionItem,
    QuestionNotSupported,
    QuestionOption,
    QuestionRequest,
)

logger = logging.getLogger(__name__)

# 工具名称
NAME = "ExitPlanMode"

# 保留的选项标签，不允许用户自定义选项使用
_RESERVED_LABELS = {"reject", "revise", "approve", "reject and exit"}


class PlanOption(BaseModel):
    """计划中的可选项/方案。

    当计划包含多个备选方案时，通过此模型列出各方案供用户选择。

    Attributes:
        label: 方案的简短名称（1-8 个词），推荐方案可添加 "(Recommended)"。
        description: 方案的简要说明和权衡分析。
    """

    label: str = Field(
        description=(
            "Short name for this option (1-8 words). "
            "Append '(Recommended)' if you recommend this option."
        ),
    )
    description: str = Field(
        default="",
        description="Brief summary of this approach and its trade-offs.",
    )

    @field_validator("label")
    @classmethod
    def label_not_reserved(cls, v: str) -> str:
        """验证标签未被保留。

        Args:
            v: 要验证的标签值。

        Returns:
            验证通过的标签值。

        Raises:
            ValueError: 标签为保留标签时抛出。
        """
        if v.strip().lower() in _RESERVED_LABELS:
            reserved = ", ".join(f"'{w.title()}'" for w in sorted(_RESERVED_LABELS))
            raise ValueError(
                f"Option label {v!r} is reserved. Do not use {reserved} as option labels."
            )
        return v


class Params(BaseModel):
    """ExitPlanMode 工具的参数模型。

    Attributes:
        options: 计划中的备选方案列表（2-3 个），每个方案代表计划中的一个独立方案。
            不允许使用 'Reject', 'Revise', 'Approve', 'Reject and Exit' 作为标签。
    """

    options: list[PlanOption] | None = Field(
        default=None,
        max_length=3,
        description=(
            "When the plan contains multiple alternative approaches, list them here "
            "so the user can choose which one to execute. 2-3 options. "
            "Each option represents a distinct approach from the plan. "
            "Do not use 'Reject', 'Revise', 'Approve', or 'Reject and Exit' as labels."
        ),
    )

    @field_validator("options")
    @classmethod
    def options_labels_unique(cls, v: list[PlanOption] | None) -> list[PlanOption] | None:
        """验证选项标签唯一性。

        Args:
            v: 选项列表。

        Returns:
            验证通过的选项列表。

        Raises:
            ValueError: 存在重复标签时抛出。
        """
        if v is None:
            return v
        labels = [opt.label for opt in v]
        if len(labels) != len(set(labels)):
            raise ValueError("Option labels must be unique. Found duplicate label(s).")
        return v


class ExitPlanMode(CallableTool2[Params]):
    """提交计划并请求用户审批的工具。

    在计划模式下完成方案设计后，通过此工具提交计划文件供用户审批。
    用户可以选择批准、拒绝或要求修订计划。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = NAME
    description: str = load_desc(Path(__file__).parent / "description.md")
    params: type[Params] = Params

    def __init__(self) -> None:
        """初始化 ExitPlanMode 工具。"""
        super().__init__()
        self._toggle_callback: Callable[[], Awaitable[bool]] | None = None
        self._plan_file_path_getter: Callable[[], Path | None] | None = None
        self._plan_mode_checker: Callable[[], bool] | None = None
        self._is_yolo: Callable[[], bool] | None = None

    def bind(
        self,
        toggle_callback: Callable[[], Awaitable[bool]],
        plan_file_path_getter: Callable[[], Path | None],
        plan_mode_checker: Callable[[], bool],
        is_yolo: Callable[[], bool] | None = None,
    ) -> None:
        """延迟绑定 Soul 回调函数。

        在 NovelSoul 构造完成后绑定回调，避免构造顺序问题。

        Args:
            toggle_callback: 切换计划模式的回调函数。
            plan_file_path_getter: 获取计划文件路径的回调函数。
            plan_mode_checker: 检查是否处于计划模式的回调函数。
            is_yolo: 检查是否为 yolo 模式（自动审批）的回调函数。
        """
        self._toggle_callback = toggle_callback
        self._plan_file_path_getter = plan_file_path_getter
        self._plan_mode_checker = plan_mode_checker
        self._is_yolo = is_yolo

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行计划提交和审批请求。

        Args:
            params: 工具参数，包含可选的备选方案列表。

        Returns:
            工具执行结果，包含审批结果和后续操作指引。

        Raises:
            QuestionNotSupported: 客户端不支持问答功能时抛出。
        """
        # 检查：仅计划模式下可用
        if not self._plan_mode_checker or not self._plan_mode_checker():
            return ToolError(
                message="Not in plan mode. ExitPlanMode is only available during plan mode.",
                brief="Not in plan mode",
            )

        if not self._toggle_callback or not self._plan_file_path_getter:
            return ToolError(
                message="ExitPlanMode is not properly initialized.",
                brief="Not initialized",
            )

        # 读取计划文件
        plan_path = self._plan_file_path_getter()
        plan_content: str | None = None
        if plan_path and await asyncio.to_thread(plan_path.exists):
            plan_content = await asyncio.to_thread(plan_path.read_text, encoding="utf-8")

        if not plan_content:
            return ToolError(
                message=f"No plan file found. Write your plan to {plan_path} first, "
                "then call ExitPlanMode.",
                brief="No plan file",
            )

        # yolo 模式下自动审批计划
        if self._is_yolo and self._is_yolo():
            await self._toggle_callback()
            return ToolReturnValue(
                is_error=False,
                output=(
                    f"Plan approved (auto-approved in non-interactive mode). "
                    f"Plan mode deactivated. All tools are now available.\n"
                    f"Plan saved to: {plan_path}\n\n"
                    f"## Approved Plan:\n{plan_content}"
                ),
                message="Plan approved (auto)",
                display=[BriefDisplayBlock(text="Plan approved (auto)")],
            )

        # 通过 QuestionRequest 向用户展示计划审批对话框
        wire = get_wire_or_none()
        if wire is None:
            return ToolError(
                message="Cannot present plan: Wire is not available.",
                brief="Wire unavailable",
            )

        tool_call = get_current_tool_call_or_none()
        if tool_call is None:
            return ToolError(
                message="ExitPlanMode must be called from a tool call context.",
                brief="Invalid context",
            )

        has_options = params.options is not None and len(params.options) >= 2

        # 拒绝选项
        _reject_options = [
            QuestionOption(
                label="Reject",
                description="Reject and stay in plan mode",
            ),
            QuestionOption(
                label="Reject and Exit",
                description="Reject and exit plan mode",
            ),
        ]

        if has_options:
            assert params.options is not None
            # 多方案选项：用户选择具体方案
            question_options = [
                QuestionOption(label=opt.label, description=opt.description)
                for opt in params.options
            ]
            question_options.extend(_reject_options)
        else:
            # 单方案选项：仅批准或拒绝
            question_options = [
                QuestionOption(
                    label="Approve",
                    description="Exit plan mode and start execution",
                ),
                *_reject_options,
            ]

        # 在聊天中内联显示计划内容
        wire_send(PlanDisplay(content=plan_content, file_path=str(plan_path)))

        request = QuestionRequest(
            id=str(uuid4()),
            tool_call_id=tool_call.id,
            questions=[
                QuestionItem(
                    question="Approve this plan",
                    header="Plan",
                    options=question_options,
                    other_label="Revise",
                    other_description="Stay in plan mode and provide feedback",
                )
            ],
        )

        wire_send(request)

        try:
            answers = await request.wait()
        except QuestionNotSupported:
            return ToolError(
                message="The connected client does not support plan mode. "
                "Do NOT call this tool again.",
                brief="Client unsupported",
            )
        except Exception:
            logger.exception("Failed to get user response for ExitPlanMode")
            return ToolError(
                message="Failed to get user response.",
                brief="Question failed",
            )

        if not answers:
            return ToolReturnValue(
                is_error=False,
                output="User dismissed without choosing. Plan mode remains active. "
                "Continue working on your plan or call ExitPlanMode again when ready.",
                message="Dismissed",
                display=[BriefDisplayBlock(text="Dismissed")],
            )

        # 解析用户选择 —— 精确匹配选项标签
        chose_reject_and_exit = any(v == "Reject and Exit" for v in answers.values())

        if chose_reject_and_exit:
            await self._toggle_callback()
            return ToolRejectedError(
                message=(
                    "Plan rejected by user. Plan mode deactivated. "
                    "All tools are now available. "
                    "Wait for the user's next message."
                ),
                brief="Plan rejected, exited plan mode",
            )

        chose_reject = any(v == "Reject" for v in answers.values())

        if chose_reject:
            return ToolRejectedError(
                message=(
                    "Plan rejected by user. Stay in plan mode. "
                    "The user will provide feedback via conversation. "
                    "Wait for the user's next message before revising."
                ),
                brief="Plan rejected",
            )

        # 批准 —— 多方案（用户选择了具体方案）
        if has_options:
            assert params.options is not None
            option_labels = {opt.label for opt in params.options}
            chosen_option = None
            for v in answers.values():
                if v in option_labels:
                    chosen_option = v
                    break

            if chosen_option:
                await self._toggle_callback()
                return ToolReturnValue(
                    is_error=False,
                    output=(
                        f'Plan approved by user. Selected approach: "{chosen_option}"\n'
                        f"Plan mode deactivated. All tools are now available.\n"
                        f"Plan saved to: {plan_path}\n\n"
                        f'IMPORTANT: Execute ONLY the selected approach "{chosen_option}". '
                        f"Ignore other approaches in the plan.\n\n"
                        f"## Approved Plan:\n{plan_content}"
                    ),
                    message=f"Plan approved: {chosen_option}",
                    display=[BriefDisplayBlock(text=f"Plan approved: {chosen_option}")],
                )

        # 批准 —— 单方案（has_options 使用选项标签而非 "Approve"）
        chose_approve = not has_options and any(v == "Approve" for v in answers.values())
        if chose_approve:
            await self._toggle_callback()
            return ToolReturnValue(
                is_error=False,
                output=(
                    f"Plan approved by user. Plan mode deactivated. "
                    f"All tools are now available.\n"
                    f"Plan saved to: {plan_path}\n\n"
                    f"## Approved Plan:\n{plan_content}"
                ),
                message="Plan approved",
                display=[BriefDisplayBlock(text="Plan approved")],
            )

        # 修订 —— 用户选择了自由文本 "Revise" 选项（回退）
        feedback = ""
        for v in answers.values():
            if v not in ("Approve", "Reject", "Reject and Exit"):
                feedback = v
        if feedback:
            msg = (
                "User wants to revise the plan. Stay in plan mode. "
                "Revise based on the feedback below.\n\n"
                f"User feedback: {feedback}"
            )
        else:
            msg = (
                "User wants to revise the plan. Stay in plan mode. "
                "Wait for the user's next message with feedback before revising."
            )
        return ToolReturnValue(
            is_error=False,
            output=msg,
            message="Plan revision requested",
            display=[BriefDisplayBlock(text="Plan revision requested")],
        )