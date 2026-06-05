"""Setup 模块共享 UI 组件。

提供选择框、文本输入、确认提示、脱敏显示等通用交互组件。
"""
from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.shortcuts.choice_input import ChoiceInput
from pydantic import SecretStr

from novel_cli.ui.shell.console import console


async def prompt_choice(
    *,
    header: str,
    options: list[tuple[str, str]],
    default: str | None = None,
) -> str | None:
    """使用 ChoiceInput 做选项交互。

    Args:
        header: 提示文字。
        options: [(value, label), ...] 的选项列表。
        default: 默认选项的 value。

    Returns:
        str: 选中项的 value。
        None: 用户取消。
    """
    if not options:
        return None

    if default is None:
        default = options[0][0]

    try:
        return await ChoiceInput(
            message=header,
            options=options,
            default=default,
        ).prompt_async()
    except (EOFError, KeyboardInterrupt):
        return None


async def prompt_text(
    prompt: str, *, is_password: bool = False
) -> str | None:
    """使用 PromptSession 做文本输入。

    Args:
        prompt: 提示文字。
        is_password: 是否为密码输入（隐藏输入内容）。

    Returns:
        str: 用户输入的文本（已去除首尾空白）。
        None: 用户取消。
    """
    session = PromptSession[str]()
    try:
        return str(
            await session.prompt_async(
                f" {prompt}: ",
                is_password=is_password,
            )
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None


async def prompt_confirm(label: str) -> bool:
    """确认提示。

    Args:
        label: 确认提示文字。

    Returns:
        bool: 用户是否确认。
    """
    choice = await prompt_choice(
        header=label,
        options=[
            ("yes", "确认"),
            ("no", "取消"),
        ],
        default="no",
    )
    return choice == "yes"


def mask_api_key(key: SecretStr | str) -> str:
    """API Key 脱敏显示。

    Args:
        key: API Key 字符串或 SecretStr 对象。

    Returns:
        str: 脱敏后的字符串。
    """
    raw = key.get_secret_value() if isinstance(key, SecretStr) else key
    if len(raw) > 6:
        return f"{raw[:3]}***...***{raw[-3:]}"
    if raw:
        return "***"
    return "(空)"


def show_config_section(title: str, fields: list[tuple[str, str]]) -> None:
    """格式化展示配置段。

    Args:
        title: 段落标题。
        fields: [(label, value), ...] 字段列表。
    """
    console.print(f"[bold]{title}[/bold]")
    for label, value in fields:
        console.print(f"  {label}: {value}")
    console.print()
