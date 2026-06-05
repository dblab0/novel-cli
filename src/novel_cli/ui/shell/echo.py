"""用户输入回显模块。

提供用户消息的回显渲染功能，用于在 Shell 中显示用户输入的文本。
"""

from __future__ import annotations

from kosong.message import Message
from rich.text import Text

from novel_cli.ui.shell.prompt import PROMPT_SYMBOL
from novel_cli.utils.message import message_stringify


def render_user_echo(message: Message) -> Text:
    """将用户消息渲染为 Shell 转录文本。

    Args:
        message: 用户消息对象。

    Returns:
        Rich Text 对象，包含提示符和消息内容。
    """
    return Text(f"{PROMPT_SYMBOL} {message_stringify(message)}")


def render_user_echo_text(text: str) -> Text:
    """将本地提示文本渲染为用户所见的形式。

    Args:
        text: 提示文本内容。

    Returns:
        Rich Text 对象，包含提示符和文本内容。
    """
    return Text(f"{PROMPT_SYMBOL} {text}")
