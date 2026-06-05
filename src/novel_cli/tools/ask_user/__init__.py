"""询问用户工具模块。

提供向用户提问并获取回答的工具功能。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import override
from uuid import uuid4

from kosong.tooling import BriefDisplayBlock, CallableTool2, ToolError, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.soul import get_wire_or_none, wire_send
from novel_cli.soul.toolset import get_current_tool_call_or_none
from novel_cli.tools.utils import load_desc
from novel_cli.wire.types import QuestionItem, QuestionNotSupported, QuestionOption, QuestionRequest

logger = logging.getLogger(__name__)

NAME = "AskUserQuestion"

_BASE_DESCRIPTION = load_desc(Path(__file__).parent / "description.md")


class QuestionOptionParam(BaseModel):
    """问题选项参数。

    Attributes:
        label: 简洁的显示文本（1-5个词），推荐选项可添加 "(Recommended)" 后缀。
        description: 选择该选项的权衡或影响的简要说明。
    """

    label: str = Field(
        description="Concise display text (1-5 words). If recommended, append '(Recommended)'."
    )
    description: str = Field(
        default="",
        description="Brief explanation of trade-offs or implications of choosing this option.",
    )


class QuestionParam(BaseModel):
    """单个问题参数。

    Attributes:
        question: 具体、可执行的问题，以问号结尾。
        header: 短分类标签（最多12个字符，如 "Auth"、"Style"）。
        options: 2-4个有意义且互不相同的选项，系统会自动添加"其他"选项。
        multi_select: 用户是否可以选择多个选项。
    """

    question: str = Field(description="A specific, actionable question. End with '?'.")
    header: str = Field(
        default="", description="Short category tag (max 12 chars, e.g. 'Auth', 'Style')."
    )
    options: list[QuestionOptionParam] = Field(
        description=(
            "2-4 meaningful, distinct options. Do NOT include an 'Other' option — "
            "the system adds one automatically."
        ),
        min_length=2,
        max_length=4,
    )
    multi_select: bool = Field(
        default=False,
        description="Whether the user can select multiple options.",
    )


class Params(BaseModel):
    """询问用户工具的参数。

    Attributes:
        questions: 要向用户提出的问题列表（1-4个问题）。
    """

    questions: list[QuestionParam] = Field(
        description="The questions to ask the user (1-4 questions).",
        min_length=1,
        max_length=4,
    )


class AskUserQuestion(CallableTool2[Params]):
    """询问用户问题工具。

    用于向用户提出问题并等待用户回答的工具。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = NAME
    description: str = _BASE_DESCRIPTION
    params: type[Params] = Params

    def __init__(self) -> None:
        super().__init__()
        self._is_yolo: Callable[[], bool] | None = None

    def bind_approval(self, is_yolo: Callable[[], bool]) -> None:
        """延迟绑定 yolo 检查器，用于在非交互模式下自动跳过。

        Args:
            is_yolo: 返回是否处于 yolo（非交互）模式的可调用对象。
        """
        self._is_yolo = is_yolo

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行询问用户问题工具。

        Args:
            params: 询问参数，包含问题列表。

        Returns:
            ToolReturnValue 包含用户的回答或错误信息。
        """
        if self._is_yolo and self._is_yolo():
            return ToolReturnValue(
                is_error=False,
                output=(
                    '{"answers": {}, "note": "Running in non-interactive'
                    ' (yolo) mode. Make your own decision."}'
                ),
                message="Non-interactive mode, auto-dismissed.",
                display=[BriefDisplayBlock(text="Auto-dismissed (yolo)")],
            )

        wire = get_wire_or_none()
        if wire is None:
            return ToolError(
                message="Cannot ask user questions: Wire is not available.",
                brief="Wire unavailable",
            )

        tool_call = get_current_tool_call_or_none()
        if tool_call is None:
            return ToolError(
                message="AskUserQuestion must be called from a tool call context.",
                brief="Invalid context",
            )

        questions = [
            QuestionItem(
                question=q.question,
                header=q.header,
                options=[
                    QuestionOption(label=o.label, description=o.description) for o in q.options
                ],
                multi_select=q.multi_select,
            )
            for q in params.questions
        ]

        request = QuestionRequest(
            id=str(uuid4()),
            tool_call_id=tool_call.id,
            questions=questions,
        )

        wire_send(request)

        try:
            answers = await request.wait()
        except QuestionNotSupported:
            return ToolError(
                message=(
                    "The connected client does not support interactive questions. "
                    "Do NOT call this tool again. "
                    "Ask the user directly in your text response instead."
                ),
                brief="Client unsupported",
            )
        except Exception:
            logger.exception("Failed to get user response for question %s", request.id)
            return ToolError(
                message="Failed to get user response.",
                brief="Question failed",
            )

        if not answers:
            return ToolReturnValue(
                is_error=False,
                output='{"answers": {}, "note": "User dismissed the question without answering."}',
                message="User dismissed the question without answering.",
                display=[BriefDisplayBlock(text="User dismissed")],
            )

        formatted = json.dumps({"answers": answers}, ensure_ascii=False)
        return ToolReturnValue(
            is_error=False,
            output=formatted,
            message="User has answered.",
            display=[BriefDisplayBlock(text="User answered")],
        )
