"""调试命令模块。

提供 /debug 命令，用于显示当前会话的上下文详情、消息历史和 token 计数等信息。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kosong.message import Message
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from novel_cli.soul.novelsoul import NovelSoul
from novel_cli.ui.shell.console import console
from novel_cli.ui.shell.slash import registry
from novel_cli.wire.types import (
    AudioURLPart,
    ContentPart,
    ImageURLPart,
    TextPart,
    ThinkPart,
    ToolCall,
    VideoURLPart,
)

if TYPE_CHECKING:
    from novel_cli.ui.shell import Shell


def _format_content_part(part: ContentPart) -> Text | Panel | Group:
    """格式化单个内容部分。

    Args:
        part: 内容部分对象。

    Returns:
        格式化后的 Rich 可渲染对象。
    """
    match part:
        case TextPart(text=text):
            # 检查是否类似系统标签
            if text.strip().startswith("<system>") and text.strip().endswith("</system>"):
                return Panel(
                    text.strip()[8:-9].strip(),
                    title="[dim]system[/dim]",
                    border_style="dim yellow",
                    padding=(0, 1),
                )
            return Text(text, style="white")

        case ThinkPart(think=think):
            return Panel(
                think,
                title="[dim]thinking[/dim]",
                border_style="dim cyan",
                padding=(0, 1),
            )

        case ImageURLPart(image_url=img):
            url_display = img.url[:80] + "..." if len(img.url) > 80 else img.url
            return Text(f"[Image] {url_display}", style="blue")

        case AudioURLPart(audio_url=audio):
            url_display = audio.url[:80] + "..." if len(audio.url) > 80 else audio.url
            id_text = f" (id: {audio.id})" if audio.id else ""
            return Text(f"[Audio{id_text}] {url_display}", style="blue")

        case VideoURLPart(video_url=video):
            url_display = video.url[:80] + "..." if len(video.url) > 80 else video.url
            return Text(f"[Video] {url_display}", style="blue")

        case _:
            return Text(f"[Unknown content type: {type(part).__name__}]", style="red")


def _format_tool_call(tool_call: ToolCall) -> Panel:
    """格式化工具调用信息。

    Args:
        tool_call: 工具调用对象。

    Returns:
        格式化后的 Rich Panel。
    """
    args = tool_call.function.arguments or "{}"
    try:
        args_formatted = json.dumps(json.loads(args, strict=False), indent=2)
        args_syntax = Syntax(args_formatted, "json", theme="monokai", padding=(0, 1))
    except json.JSONDecodeError:
        args_syntax = Text(args, style="red")

    content = Group(
        Text(f"Function: {tool_call.function.name}", style="bold cyan"),
        Text(f"Call ID: {tool_call.id}", style="dim"),
        Text("Arguments:", style="bold"),
        args_syntax,
    )

    return Panel(
        content,
        title="[bold yellow]Tool Call[/bold yellow]",
        border_style="yellow",
        padding=(0, 1),
    )


def _format_message(msg: Message, index: int) -> Panel:
    """格式化单条消息。

    Args:
        msg: 消息对象。
        index: 消息索引。

    Returns:
        格式化后的 Rich Panel。
    """
    # 角色样式
    role_colors = {
        "system": "magenta",
        "developer": "magenta",
        "user": "green",
        "assistant": "blue",
        "tool": "yellow",
    }
    role_color = role_colors.get(msg.role, "white")
    role_text = f"[bold {role_color}]{msg.role.upper()}[/bold {role_color}]"

    # 若存在 name 则添加
    if msg.name:
        role_text += f" [dim]({msg.name})[/dim]"

    # 为 tool 消息添加工具调用 ID
    if msg.tool_call_id:
        role_text += f" [dim]→ {msg.tool_call_id}[/dim]"

    # 格式化内容
    content_items: list[RenderableType] = []

    for part in msg.content:
        formatted = _format_content_part(part)
        content_items.append(formatted)

    # 若存在工具调用则添加
    if msg.tool_calls:
        if content_items:
            content_items.append(Text())  # 空行
        for tool_call in msg.tool_calls:
            content_items.append(_format_tool_call(tool_call))

    # 合并所有内容
    if not content_items:
        content_items.append(Text("[empty message]", style="dim italic"))

    group = Group(*content_items)

    # 创建面板
    title = f"#{index + 1} {role_text}"
    if msg.partial:
        title += " [dim italic](partial)[/dim italic]"

    return Panel(
        group,
        title=title,
        border_style=role_color,
        padding=(0, 1),
    )


@registry.command
def debug(app: Shell, args: str):
    """调试当前上下文。

    显示会话上下文的详细信息，包括消息总数、token 计数、检查点数量等，
    以及完整的消息历史列表。

    Args:
        app: Shell 应用实例。
        args: 命令参数（未使用）。
    """
    assert isinstance(app.soul, NovelSoul)

    context = app.soul.context
    history = context.history

    if not history:
        console.print(
            Panel(
                "Context is empty - no messages yet",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        return

    # 构建调试输出
    output_items = [
        Panel(
            Group(
                Text(f"Total messages: {len(history)}", style="bold"),
                Text(f"Token count: {context.token_count:,}", style="bold"),
                Text(f"Checkpoints: {context.n_checkpoints}", style="bold"),
                Text(f"Trajectory: {context.file_backend}", style="dim"),
            ),
            title="[bold]Context Info[/bold]",
            border_style="cyan",
            padding=(0, 1),
        ),
        Rule(style="dim"),
    ]

    # 添加所有消息
    for idx, msg in enumerate(history):
        output_items.append(_format_message(msg, idx))

    # 使用 rich pager 显示
    display_group = Group(*output_items)

    # 使用 pager 显示
    with console.pager(styles=True):
        console.print(display_group)
