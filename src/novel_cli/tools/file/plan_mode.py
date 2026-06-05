"""计划模式辅助模块。

提供计划模式下文件编辑目标的检查和验证功能。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kaos.path import KaosPath
from kosong.tooling import ToolError


@dataclass(frozen=True)
class PlanEditTarget:
    """计划编辑目标信息。

    用于描述文件编辑是否针对当前计划工件。

    Attributes:
        active: 计划模式是否激活。
        plan_path: 当前计划文件的路径，可能为 None。
        is_plan_target: 编辑是否针对计划文件。
    """

    active: bool
    plan_path: Path | None
    is_plan_target: bool


def inspect_plan_edit_target(
    path: KaosPath,
    *,
    plan_mode_checker: Callable[[], bool] | None,
    plan_file_path_getter: Callable[[], Path | None] | None,
) -> PlanEditTarget | ToolError:
    """检查文件编辑是否针对当前计划工件。

    在计划模式下，验证文件编辑是否仅针对当前计划文件，
    以确保计划模式的编辑限制。

    Args:
        path: 要编辑的文件路径。
        plan_mode_checker: 检查计划模式是否激活的可调用对象。
        plan_file_path_getter: 获取当前计划文件路径的可调用对象。

    Returns:
        如果验证成功，返回 PlanEditTarget 对象描述编辑目标信息；
        如果计划模式下编辑非计划文件，返回 ToolError 表示错误。
    """
    if plan_mode_checker is None or not plan_mode_checker():
        return PlanEditTarget(active=False, plan_path=None, is_plan_target=False)

    plan_path = plan_file_path_getter() if plan_file_path_getter is not None else None
    if plan_path is None:
        return ToolError(
            message="Plan mode is active, but the current plan file is unavailable.",
            brief="Plan file unavailable",
        )

    canonical_plan_path = KaosPath(str(plan_path)).canonical()
    if str(path) != str(canonical_plan_path):
        return ToolError(
            message=(
                "Plan mode is active. You may only edit the current plan file: "
                f"`{canonical_plan_path}`."
            ),
            brief="Plan mode restriction",
        )

    return PlanEditTarget(active=True, plan_path=plan_path, is_plan_target=True)
