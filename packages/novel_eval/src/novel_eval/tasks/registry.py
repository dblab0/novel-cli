"""评估任务注册表。

提供 @register_task 装饰器和任务查询功能，消除 CLI 中的 if-elif 路由。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novel_eval.tasks.base import BaseTask

_TASK_REGISTRY: dict[str, type[BaseTask]] = {}


def register_task(name: str):
    """装饰器：将 Task 类注册到全局注册表。

    Args:
        name: 任务名称，如 "tool_usage"。

    Returns:
        装饰器函数。
    """
    def wrapper(cls):
        _TASK_REGISTRY[name] = cls
        return cls
    return wrapper


def get_task_cls(name: str) -> type["BaseTask"]:
    """获取注册的任务类。

    Args:
        name: 任务名称。

    Returns:
        对应的 Task 类。

    Raises:
        ValueError: 任务名称未注册时抛出。
    """
    if name not in _TASK_REGISTRY:
        available = ", ".join(sorted(_TASK_REGISTRY.keys()))
        raise ValueError(f"未知任务类型: {name}，可选: {available}")
    return _TASK_REGISTRY[name]


def list_tasks() -> list[str]:
    """返回所有已注册任务名称列表。

    Returns:
        任务名称列表。
    """
    return list(_TASK_REGISTRY.keys())


def _auto_discover() -> None:
    """导入所有 task 模块以触发 @register_task 装饰器。"""
    from novel_eval.tasks import tool_usage  # noqa: F401
    from novel_eval.tasks import skill_generation  # noqa: F401


_auto_discover()
