"""后台任务 ID 生成模块。

提供根据任务类型生成唯一任务 ID 的功能。
"""

from __future__ import annotations

import secrets

from .models import TaskKind

# 用于生成 ID 的字符集
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

# 各类型任务 ID 的前缀
_TASK_ID_PREFIXES: dict[TaskKind, str] = {
    "bash": "bash",
    "agent": "agent",
}


def generate_task_id(kind: TaskKind) -> str:
    """生成指定类型的唯一任务 ID。

    Args:
        kind: 任务类型，支持 "bash" 或 "agent"。

    Returns:
        格式为 "<前缀>-<随机后缀>" 的唯一任务 ID。

    Example:
        >>> generate_task_id("bash")
        "bash-a1b2c3d4"
    """
    prefix = _TASK_ID_PREFIXES[kind]
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{prefix}-{suffix}"
