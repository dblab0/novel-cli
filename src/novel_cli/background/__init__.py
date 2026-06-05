"""后台任务模块。

提供后台任务的管理、存储和执行功能。支持 Bash 命令和 Agent 两种类型的后台任务。
"""

from .ids import generate_task_id
from .manager import BackgroundTaskManager
from .models import (
    TaskConsumerState,
    TaskControl,
    TaskKind,
    TaskOutputChunk,
    TaskRuntime,
    TaskSpec,
    TaskStatus,
    TaskView,
    is_terminal_status,
)
from .store import BackgroundTaskStore
from .summary import build_active_task_snapshot, format_task, format_task_list, list_task_views
from .worker import run_background_task_worker

__all__ = [
    "BackgroundTaskManager",
    "BackgroundTaskStore",
    "TaskConsumerState",
    "TaskControl",
    "TaskKind",
    "TaskOutputChunk",
    "TaskRuntime",
    "TaskSpec",
    "TaskStatus",
    "TaskView",
    "build_active_task_snapshot",
    "format_task",
    "format_task_list",
    "generate_task_id",
    "is_terminal_status",
    "list_task_views",
    "run_background_task_worker",
]
