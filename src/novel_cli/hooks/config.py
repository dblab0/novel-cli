"""Hook 配置模块，定义钩子事件类型和钩子定义模型。"""

from typing import Literal

from pydantic import BaseModel, Field

HookEventType = Literal[
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "UserPromptSubmit",
    "Stop",
    "StopFailure",
    "SessionStart",
    "SessionEnd",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "Notification",
]

HOOK_EVENT_TYPES: list[str] = list(HookEventType.__args__)  # type: ignore[attr-defined]


class HookDef(BaseModel):
    """config.toml 中的单个钩子定义。

    Attributes:
        event: 触发此钩子的生命周期事件类型。
        command: 要执行的 Shell 命令，从 stdin 接收 JSON 输入。
        matcher: 用于过滤的正则表达式模式，空字符串匹配所有内容。
        timeout: 执行超时时间（秒），超时时采用 fail-open 策略。
    """

    event: HookEventType
    """Which lifecycle event triggers this hook."""
    command: str
    """Shell command to execute. Receives JSON on stdin."""
    matcher: str = ""
    """Regex pattern to filter. Empty matches everything."""
    timeout: int = Field(default=30, ge=1, le=600)
    """Timeout in seconds. Fail-open on timeout."""