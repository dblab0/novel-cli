"""EnterPlanMode 工具 —— 让 LLM 请求进入计划模式。

计划模式下，智能体只能读取文件和编写计划，不能修改代码文件。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import override
from uuid import uuid4

from kosong.tooling import BriefDisplayBlock, CallableTool2, ToolError, ToolReturnValue
from pydantic import BaseModel

from novel_cli.soul import get_wire_or_none, wire_send
from novel_cli.soul.toolset import get_current_tool_call_or_none
from novel_cli.tools.utils import load_desc
from novel_cli.wire.types import QuestionItem, QuestionNotSupported, QuestionOption, QuestionRequest

logger = logging.getLogger(__name__)

# 工具名称
NAME = "EnterPlanMode"

# 工具描述
_DESCRIPTION = load_desc(Path(__file__).parent / "enter_description.md")


class Params(BaseModel):
    """EnterPlanMode 工具的参数模型。

    该工具不需要任何参数。
    """

    pass


class EnterPlanMode(CallableTool2[Params]):
    """请求进入计划模式的工具。

    通过此工具，LLM 可以请求进入计划模式，在计划模式下进行代码探索和方案设计，
    完成计划后通过 ExitPlanMode 工具提交计划供用户审批。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = NAME
    description: str = _DESCRIPTION
    params: type[Params] = Params

    def __init__(self) -> None:
        """初始化 EnterPlanMode 工具。"""
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
        """执行进入计划模式请求。

        Args:
            params: 工具参数（无参数）。

        Returns:
            工具执行结果，包含计划模式状态和操作指引。

        Raises:
            QuestionNotSupported: 客户端不支持问答功能时抛出。
        """
        # 检查：已处于计划模式
        if self._plan_mode_checker and self._plan_mode_checker():
            return ToolError(
                message="Already in plan mode. Use ExitPlanMode when your plan is ready.",
                brief="Already in plan mode",
            )

        if not self._toggle_callback or not self._plan_file_path_getter:
            return ToolError(
                message="EnterPlanMode is not properly initialized.",
                brief="Not initialized",
            )

        # yolo 模式下自动审批进入计划模式
        if self._is_yolo and self._is_yolo():
            await self._toggle_callback()
            plan_path = self._plan_file_path_getter()
            return ToolReturnValue(
                is_error=False,
                output=(
                    f"Plan mode activated (auto-approved in non-interactive mode).\n"
                    f"Plan file: {plan_path}\n"
                    f"Workflow: identify key questions about the codebase → "
                    f"use Agent(subagent_type='explore') to investigate if needed → "
                    f"design approach → "
                    f"modify the plan file with WriteFile or StrReplaceFile "
                    f"(create it with WriteFile first if it does not exist) → "
                    f"call ExitPlanMode.\n"
                ),
                message="Plan mode on (auto)",
                display=[BriefDisplayBlock(text="Plan mode on (auto)")],
            )

        # 通过 QuestionRequest 向用户展示确认对话框
        wire = get_wire_or_none()
        if wire is None:
            return ToolError(
                message="Cannot request user confirmation: Wire is not available.",
                brief="Wire unavailable",
            )

        tool_call = get_current_tool_call_or_none()
        if tool_call is None:
            return ToolError(
                message="EnterPlanMode must be called from a tool call context.",
                brief="Invalid context",
            )

        request = QuestionRequest(
            id=str(uuid4()),
            tool_call_id=tool_call.id,
            questions=[
                QuestionItem(
                    question="Enter plan mode?",
                    header="Plan Mode",
                    options=[
                        QuestionOption(
                            label="Yes",
                            description="Enter plan mode to explore and design an approach",
                        ),
                        QuestionOption(
                            label="No",
                            description="Skip planning, start implementing now",
                        ),
                    ],
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
            logger.exception("Failed to get user response for EnterPlanMode")
            return ToolError(
                message="Failed to get user response.",
                brief="Question failed",
            )

        if not answers:
            return ToolReturnValue(
                is_error=False,
                output="User dismissed without choosing. Proceed with implementation directly.",
                message="Dismissed",
                display=[BriefDisplayBlock(text="Dismissed")],
            )

        # 解析用户选择 —— 精确匹配选项标签
        chose_yes = any(v == "Yes" for v in answers.values())
        if chose_yes:
            await self._toggle_callback()
            plan_path = self._plan_file_path_getter()
            return ToolReturnValue(
                is_error=False,
                output=(
                    f"Plan mode activated. You MUST NOT edit code files — only read and plan.\n"
                    f"Plan file: {plan_path}\n"
                    f"Workflow: identify key questions about the codebase → "
                    f"use Agent(subagent_type='explore') to investigate if needed → "
                    f"design approach → "
                    f"modify the plan file with WriteFile or StrReplaceFile "
                    f"(create it with WriteFile first if it does not exist) → "
                    f"call ExitPlanMode.\n"
                    f"Use AskUserQuestion only to clarify missing requirements or choose "
                    f"between approaches.\n"
                    f"Do NOT use AskUserQuestion to ask about plan approval."
                ),
                message="Plan mode on",
                display=[BriefDisplayBlock(text="Plan mode on")],
            )
        else:
            return ToolReturnValue(
                is_error=False,
                output=(
                    "User declined to enter plan mode. Please check with user whether "
                    "to proceed with implementation directly."
                ),
                message="Declined",
                display=[BriefDisplayBlock(text="Declined")],
            )