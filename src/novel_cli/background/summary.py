"""后台任务摘要模块。

提供任务列表的查询、格式化和快照生成功能，用于向用户展示任务状态。
"""

from __future__ import annotations

from .manager import BackgroundTaskManager
from .models import TaskView, is_terminal_status


def list_task_views(
    manager: BackgroundTaskManager,
    *,
    active_only: bool = True,
    limit: int = 20,
) -> list[TaskView]:
    """查询任务视图列表。

    Args:
        manager: 后台任务管理器。
        active_only: 是否仅返回活跃（非终态）任务。
        limit: 返回结果的最大数量。

    Returns:
        任务视图列表，按更新时间降序排列。
    """
    views = manager.list_tasks(limit=None)
    if active_only:
        views = [view for view in views if not is_terminal_status(view.runtime.status)]
    return views[:limit]


def format_task(view: TaskView, *, include_command: bool = False) -> str:
    """格式化单个任务信息为文本。

    Args:
        view: 任务视图。
        include_command: 是否包含命令内容。

    Returns:
        格式化后的任务信息文本。
    """
    lines = [
        f"task_id: {view.spec.id}",
        f"kind: {view.spec.kind}",
        f"status: {view.runtime.status}",
        f"description: {view.spec.description}",
    ]
    if view.spec.kind == "agent" and view.spec.kind_payload:
        if agent_id := view.spec.kind_payload.get("agent_id"):
            lines.append(f"agent_id: {agent_id}")
        if subagent_type := view.spec.kind_payload.get("subagent_type"):
            lines.append(f"subagent_type: {subagent_type}")
    if include_command and view.spec.command:
        lines.append(f"command: {view.spec.command}")
    if view.runtime.exit_code is not None:
        lines.append(f"exit_code: {view.runtime.exit_code}")
    if view.runtime.failure_reason:
        lines.append(f"reason: {view.runtime.failure_reason}")
    return "\n".join(lines)


def format_task_list(
    views: list[TaskView],
    *,
    active_only: bool = True,
    include_command: bool = True,
) -> str:
    """格式化任务列表为文本。

    Args:
        views: 任务视图列表。
        active_only: 标题是否显示"活跃任务"。
        include_command: 是否包含命令内容。

    Returns:
        格式化后的任务列表文本。
    """
    header = "active_background_tasks" if active_only else "background_tasks"
    if not views:
        return f"{header}: 0\n[no tasks]"

    lines = [f"{header}: {len(views)}", ""]
    for index, view in enumerate(views, start=1):
        lines.extend([f"[{index}]", format_task(view, include_command=include_command), ""])
    return "\n".join(lines).rstrip()


def build_active_task_snapshot(manager: BackgroundTaskManager, *, limit: int = 20) -> str | None:
    """构建活跃任务快照文本。

    用于向 Agent 提供当前活跃后台任务的简要状态信息。

    Args:
        manager: 后台任务管理器。
        limit: 最大任务数量。

    Returns:
        格式化后的快照文本，无活跃任务时返回 None。
    """
    views = list_task_views(manager, active_only=True, limit=limit)
    if not views:
        return None
    return "\n".join(
        [
            "<active-background-tasks>",
            format_task_list(views, active_only=True, include_command=False),
            "</active-background-tasks>",
        ]
    )
