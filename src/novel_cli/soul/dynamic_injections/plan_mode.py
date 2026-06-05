"""计划模式动态注入模块。

提供计划模式下的只读提醒注入功能。当计划模式激活时，
周期性地向 LLM 步骤注入提醒信息，确保 AI 遵守计划模式的约束。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from kosong.message import Message, TextPart

from novel_cli.soul.dynamic_injection import DynamicInjection, DynamicInjectionProvider

if TYPE_CHECKING:
    from novel_cli.soul.novelsoul import NovelSoul

# 每隔 N 个 assistant 轮次注入一次提醒。
_TURN_INTERVAL = 5
# 每 N 次提醒中，只有一次是完整版本，其余是精简版本。
_FULL_EVERY_N = 5


class PlanModeInjectionProvider(DynamicInjectionProvider):
    """计划模式激活时周期性注入只读提醒。

    通过扫描历史记录实现节流：向后扫描到最后一个计划模式提醒，
    并计算期间的 assistant 消息数量。仅当计数超过 ``_TURN_INTERVAL`` 时才注入。

    Args:
        无。

    Attributes:
        _inject_count: 注入计数器，用于确定是否注入完整版本。
    """

    def __init__(self) -> None:
        self._inject_count: int = 0

    async def get_injections(
        self,
        history: Sequence[Message],
        soul: NovelSoul,
    ) -> list[DynamicInjection]:
        """获取待注入的动态注入内容。

        根据计划模式状态和历史记录，决定是否注入以及注入哪种类型的提醒。

        Args:
            history: 消息历史记录序列。
            soul: Novel Soul 实例，用于访问运行时状态。

        Returns:
            待注入的动态注入列表，可能为空列表。
        """
        if not soul.plan_mode:
            self._inject_count = 0
            return []

        plan_path = soul.get_plan_file_path()
        plan_path_str = str(plan_path) if plan_path else None
        plan_exists = plan_path is not None and plan_path.exists()

        # 手动切换会安排一次性的激活提醒，用于下一个 LLM 步骤。
        if soul.consume_pending_plan_activation_injection():
            self._inject_count = 1
            # 重新进入时如果已存在计划文件，使用重入提醒。
            if plan_exists:
                return [
                    DynamicInjection(
                        type="plan_mode_reentry",
                        content=_reentry_reminder(plan_path_str),
                    )
                ]
            return [
                DynamicInjection(
                    type="plan_mode",
                    content=_full_reminder(plan_path_str, plan_exists),
                )
            ]

        # 向后扫描历史记录以查找最后一个计划模式提醒。
        turns_since_last = 0
        found_previous = False
        for msg in reversed(history):
            if msg.role == "user" and _has_plan_reminder(msg):
                found_previous = True
                break
            if msg.role == "assistant":
                turns_since_last += 1

        # 首次（历史记录中尚无提醒）-> 注入完整版本。
        if not found_previous:
            self._inject_count = 1
            return [
                DynamicInjection(
                    type="plan_mode",
                    content=_full_reminder(plan_path_str, plan_exists),
                )
            ]

        # 自上次提醒以来的轮次不足 -> 跳过。
        if turns_since_last < _TURN_INTERVAL:
            return []

        # 执行注入。
        self._inject_count += 1
        is_full = self._inject_count % _FULL_EVERY_N == 1
        if is_full:
            content = _full_reminder(plan_path_str, plan_exists)
        else:
            content = _sparse_reminder(plan_path_str)
        return [DynamicInjection(type="plan_mode", content=content)]


def _has_plan_reminder(msg: Message) -> bool:
    """检查消息是否包含计划模式提醒。

    通过匹配实际提醒文本的稳定前缀来检测，这样提醒措辞的更改可以自动保持同步。

    Args:
        msg: 待检查的消息。

    Returns:
        如果消息包含计划模式提醒则返回 True，否则返回 False。
    """
    keys = (
        _sparse_reminder().split(".")[0],  # "Plan mode still active ..."
        _full_reminder().split("\n")[0],  # "Plan mode is active. ..."
    )
    for part in msg.content:
        if isinstance(part, TextPart) and any(key in part.text for key in keys):
            return True
    return False


def _full_reminder(
    plan_file_path: str | None = None,
    plan_exists: bool = False,
) -> str:
    """生成完整的计划模式提醒文本。

    包含详细的计划模式说明、工作流程和多方案处理指南。

    Args:
        plan_file_path: 计划文件路径，可选。
        plan_exists: 计划文件是否已存在。

    Returns:
        完整的计划模式提醒文本。
    """
    lines = [
        "Plan mode is active. You MUST NOT make any edits "
        "(with the exception of the plan file below), run non-readonly tools, "
        "or otherwise make changes to the system. "
        "This supersedes any other instructions you have received.",
    ]
    # 计划文件信息块
    if plan_file_path:
        lines.append("")
        if plan_exists:
            lines.append(
                f"Plan file: {plan_file_path} "
                "(exists — read first, then update it with WriteFile or StrReplaceFile)"
            )
        else:
            lines.append(
                f"Plan file: {plan_file_path} "
                "(create it with WriteFile; once it exists, you can modify it with "
                "WriteFile or StrReplaceFile)"
            )
        lines.append("This is the only file you are allowed to edit.")
    # 工作流程
    lines.extend(
        [
            "",
            "Workflow:",
            "1. Understand — explore the codebase with Glob, Grep, ReadFile",
            "2. Design — converge on the best approach; "
            "consider trade-offs but aim for a single recommendation",
            "3. Review — re-read key files to verify understanding",
            "4. Write Plan — modify the plan file with WriteFile or StrReplaceFile. "
            "Use WriteFile if the plan file does not exist yet",
            "5. Exit — call ExitPlanMode for user approval",
        ]
    )
    # 多方案处理
    lines.extend(
        [
            "",
            "## Handling multiple approaches",
            "Keep it focused: at most 2-3 meaningfully different approaches. "
            "Do NOT pad with minor variations — if one approach is clearly "
            "superior, just propose that one.",
            "When the best approach depends on user preferences, constraints, "
            "or context you don't have, use AskUserQuestion to clarify first. "
            "This helps you write a better, more targeted plan rather than "
            "dumping multiple options for the user to sort through.",
            "When you do include multiple approaches in the plan, you MUST pass them "
            "as the `options` parameter when calling ExitPlanMode, so the user can select which "
            "approach to execute at approval time.",
            "NEVER write multiple approaches in the plan and call ExitPlanMode without the "
            "`options` parameter — the user will only see Approve/Reject with no way to choose.",
        ]
    )
    # 轮次结束约束 + 反模式
    lines.extend(
        [
            "",
            "AskUserQuestion is for clarifying missing requirements or user preferences "
            "that affect the plan.",
            "Never ask about plan approval via text or AskUserQuestion.",
            "Your turn must end with either AskUserQuestion "
            "(to clarify requirements or preferences) "
            "or ExitPlanMode (to request plan approval). "
            "Do NOT end your turn any other way.",
            "Do NOT use AskUserQuestion to ask about plan approval or reference "
            '"the plan" — the user cannot see the plan until you call ExitPlanMode.',
        ]
    )
    return "\n".join(lines)


def _sparse_reminder(plan_file_path: str | None = None) -> str:
    """生成精简的计划模式提醒文本。

    仅包含简短的状态提醒和核心操作提示。

    Args:
        plan_file_path: 计划文件路径，可选。

    Returns:
        精简的计划模式提醒文本。
    """
    parts = [
        "Plan mode still active (see full instructions earlier).",
    ]
    if plan_file_path:
        parts.append(f"Read-only except plan file ({plan_file_path}).")
    else:
        parts.append("Read-only.")
    parts.extend(
        [
            "Use WriteFile or StrReplaceFile to modify the plan file. "
            "If it does not exist yet, create it with WriteFile first.",
            "Use AskUserQuestion to clarify user preferences "
            "when it helps you write a better plan.",
            "If the plan has multiple approaches, "
            "pass options to ExitPlanMode so the user can choose.",
            "End turns with AskUserQuestion (for clarifications) or ExitPlanMode (for approval).",
            "Never ask about plan approval via text or AskUserQuestion.",
        ]
    )
    return " ".join(parts)


def _reentry_reminder(plan_file_path: str | None = None) -> str:
    """生成计划模式重入提醒文本。

    当重新进入计划模式且已存在计划文件时使用的一次性提醒。

    Args:
        plan_file_path: 计划文件路径，可选。

    Returns:
        重入计划模式的提醒文本。
    """
    lines = [
        "Plan mode is active. You MUST NOT make any edits "
        "(with the exception of the plan file below), run non-readonly tools, "
        "or otherwise make changes to the system. "
        "This supersedes any other instructions you have received.",
        "",
        "## Re-entering Plan Mode",
        (
            f"A plan file exists at {plan_file_path} from a previous planning session."
            if plan_file_path
            else "A plan file from a previous planning session already exists."
        ),
        "Before proceeding:",
        "1. Read the existing plan file to understand what was previously planned",
        "2. Evaluate the user's current request against that plan",
        "3. If different task: replace the old plan with a fresh one. "
        "If same task: update the existing plan.",
        "4. You may use WriteFile or StrReplaceFile to modify the plan file. "
        "If the file does not exist yet, create it with WriteFile first.",
        "5. Use AskUserQuestion to clarify missing requirements "
        "or user preferences that affect the plan.",
        "6. Always edit the plan file before calling ExitPlanMode.",
        "",
        "Your turn must end with either AskUserQuestion (to clarify requirements) "
        "or ExitPlanMode (to request plan approval).",
    ]
    return "\n".join(lines)