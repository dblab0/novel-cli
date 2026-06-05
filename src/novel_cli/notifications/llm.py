"""通知 LLM 消息构建模块。

提供将通知转换为 LLM 可理解的消息格式，以及从历史记录中提取通知的功能。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from kosong.message import Message

from novel_cli.wire.types import TextPart

from .models import NotificationView

if TYPE_CHECKING:
    from novel_cli.soul.agent import Runtime

# 用于匹配通知 ID 的正则表达式
_NOTIFICATION_ID_RE = re.compile(r'<notification id="([^"]+)"')


def build_notification_message(view: NotificationView, runtime: Runtime) -> Message:
    """构建通知消息，转换为 LLM 可理解的格式。

    Args:
        view: 通知视图对象。
        runtime: 运行时环境，用于获取后台任务详情。

    Returns:
        构建好的用户消息，包含通知内容。
    """
    event = view.event
    lines = [
        (
            f'<notification id="{event.id}" category="{event.category}" '
            f'type="{event.type}" source_kind="{event.source_kind}" source_id="{event.source_id}">'
        ),
        f"Title: {event.title}",
        f"Severity: {event.severity}",
        event.body,
    ]

    # 如果是后台任务通知，附加任务详情和输出
    if event.category == "task" and event.source_kind == "background_task":
        task_view = runtime.background_tasks.get_task(event.source_id)
        if task_view is not None:
            tail = runtime.background_tasks.tail_output(
                task_view.spec.id,
                max_bytes=runtime.config.background.notification_tail_chars,
                max_lines=runtime.config.background.notification_tail_lines,
            )
            lines.extend(
                [
                    "<task-notification>",
                    f"Task ID: {task_view.spec.id}",
                    f"Task Type: {task_view.spec.kind}",
                    f"Description: {task_view.spec.description}",
                    f"Status: {task_view.runtime.status}",
                ]
            )
            if task_view.runtime.exit_code is not None:
                lines.append(f"Exit code: {task_view.runtime.exit_code}")
            if task_view.runtime.failure_reason:
                lines.append(f"Failure reason: {task_view.runtime.failure_reason}")
            if tail:
                lines.extend(["Output tail:", tail])
            lines.append("</task-notification>")

    lines.append("</notification>")
    return Message(role="user", content=[TextPart(text="\n".join(lines))])


def extract_notification_ids(history: Sequence[Message]) -> set[str]:
    """从历史消息中提取所有通知 ID。

    Args:
        history: 消息历史记录序列。

    Returns:
        所有已处理通知 ID 的集合。
    """
    ids: set[str] = set()
    for message in history:
        if message.role != "user":
            continue
        for part in message.content:
            if not isinstance(part, TextPart):
                continue
            for match in _NOTIFICATION_ID_RE.finditer(part.text):
                ids.add(match.group(1))
    return ids


def is_notification_message(message: Message) -> bool:
    """检查消息是否为通知消息。

    Args:
        message: 待检查的消息。

    Returns:
        如果是通知消息返回 True，否则返回 False。
    """
    if message.role != "user" or len(message.content) != 1:
        return False
    part = message.content[0]
    return isinstance(part, TextPart) and part.text.lstrip().startswith("<notification ")
