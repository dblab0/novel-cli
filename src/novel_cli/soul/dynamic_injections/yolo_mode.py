"""YOLO 模式动态注入模块。

当 YOLO 模式激活时，注入一次性提醒，告知 AI 在非交互模式下运行，
无需等待用户反馈。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from kosong.message import Message

from novel_cli.soul.dynamic_injection import DynamicInjection, DynamicInjectionProvider

if TYPE_CHECKING:
    from novel_cli.soul.novelsoul import NovelSoul

_YOLO_INJECTION_TYPE = "yolo_mode"

_YOLO_PROMPT = (
    "You are running in non-interactive mode. The user cannot answer questions "
    "or provide feedback during execution.\n"
    "- Do NOT call AskUserQuestion. If you need to make a decision, make your "
    "best judgment and proceed.\n"
    "- For EnterPlanMode / ExitPlanMode, they will be auto-approved. You can use "
    "them normally but expect no user feedback."
)


class YoloModeInjectionProvider(DynamicInjectionProvider):
    """YOLO 模式激活时注入一次性提醒。

    Args:
        无。

    Attributes:
        _injected: 是否已注入过提醒。
    """

    def __init__(self) -> None:
        self._injected: bool = False

    async def get_injections(
        self,
        history: Sequence[Message],
        soul: NovelSoul,
    ) -> list[DynamicInjection]:
        """获取待注入的动态注入内容。

        仅在 YOLO 模式激活且尚未注入时返回提醒内容。

        Args:
            history: 消息历史记录序列。
            soul: NovelSoul 实例，用于检查 YOLO 模式状态。

        Returns:
            待注入的动态注入列表，可能为空列表。
        """
        if not soul.is_yolo:
            return []
        if self._injected:
            return []
        self._injected = True
        return [DynamicInjection(type=_YOLO_INJECTION_TYPE, content=_YOLO_PROMPT)]