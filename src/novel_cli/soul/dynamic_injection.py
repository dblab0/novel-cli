"""动态注入模块。

定义动态注入的数据结构和基类，用于在 LLM 步骤之前动态注入提示内容。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kosong.message import Message

from novel_cli.notifications import is_notification_message

if TYPE_CHECKING:
    from novel_cli.soul.novelsoul import NovelSoul


@dataclass(frozen=True, slots=True)
class DynamicInjection:
    """待注入到 LLM 步骤之前的动态提示内容。

    Attributes:
        type: 注入类型标识符，例如 "plan_mode"。
        content: 文本内容（将被包装在 <system-reminder> 标签中）。
    """

    type: str  # 标识符，例如 "plan_mode"
    content: str  # 文本内容（将被包装在 <system-reminder> 标签中）


class DynamicInjectionProvider(ABC):
    """动态注入提供者的基类。

    在每个 LLM 步骤之前被调用。实现类负责处理自己的节流逻辑。
    提供者可以通过 ``soul`` 参数访问所有运行时状态
    （context_usage、runtime、config 等）。
    """

    @abstractmethod
    async def get_injections(
        self,
        history: Sequence[Message],
        soul: NovelSoul,
    ) -> list[DynamicInjection]: ...

    """获取待注入的动态注入内容列表。

    Args:
        history: 消息历史记录序列。
        soul: NovelSoul 实例，用于访问运行时状态。

    Returns:
        待注入的动态注入列表。
    """


def normalize_history(history: Sequence[Message]) -> list[Message]:
    """合并相邻的用户消息以生成干净的 API 输入序列。

    动态注入在历史记录中存储为独立的用户消息；
    规范化将它们合并到相邻的用户消息中。

    只有 ``user`` 角色的消息会被合并。assistant 和 tool 消息
    永远不会被合并，因为它们的 ``tool_calls`` / ``tool_call_id``
    字段形成必须保持完整的链接对。

    Args:
        history: 原始消息历史记录序列。

    Returns:
        规范化后的消息列表。
    """
    if not history:
        return []

    result: list[Message] = []
    for msg in history:
        if (
            result
            and result[-1].role == msg.role
            and msg.role == "user"
            and not is_notification_message(result[-1])
            and not is_notification_message(msg)
        ):
            merged_content = list(result[-1].content) + list(msg.content)
            result[-1] = Message(role="user", content=merged_content)
        else:
            result.append(msg)
    return result
