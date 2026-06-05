"""控制台输出模块。

提供自定义 Console 和 Pager 实现，用于处理 ANSI 颜色输出和 OSC 8 超链接等特殊格式。
"""

from __future__ import annotations

import os
import pydoc
import re

from rich.console import Console, PagerContext, RenderableType
from rich.pager import Pager
from rich.theme import Theme

NEUTRAL_MARKDOWN_THEME = Theme(
    {
        "markdown.paragraph": "none",
        "markdown.block_quote": "none",
        "markdown.hr": "none",
        "markdown.list": "none",
        "markdown.item": "none",
        "markdown.item.bullet": "none",
        "markdown.item.number": "none",
        "markdown.link": "none",
        "markdown.link_url": "none",
        "markdown.h1": "none",
        "markdown.h1.border": "none",
        "markdown.h2": "none",
        "markdown.h3": "none",
        "markdown.h4": "none",
        "markdown.h5": "none",
        "markdown.h6": "none",
        "markdown.h7": "none",
        "markdown.em": "none",
        "markdown.emph": "none",
        "markdown.strong": "none",
        "markdown.s": "none",
        "markdown.code": "none",
        "markdown.code_block": "none",
        "status.spinner": "none",
    },
    inherit=True,
)

_NEUTRAL_MARKDOWN_THEME = NEUTRAL_MARKDOWN_THEME


class _NovelPager(Pager):
    """忽略 MANPAGER 的 Pager 实现。

    ``pydoc.getpager()`` 会先读取 ``MANPAGER`` 再读取 ``PAGER``。
    当用户将 ``MANPAGER`` 设置为 man 专用管道（如 ``sh -c 'col -bx | bat -l man -p'``）时，
    该管道会破坏我们输出的 ANSI rich-text。此 Pager 从子进程环境中移除 ``MANPAGER``，
    使其只使用 ``PAGER``（或默认的 ``less``）。

    Attributes:
        无显式属性。
    """

    def show(self, content: str) -> None:
        """显示内容。

        Args:
            content: 待显示的文本内容。
        """
        saved = os.environ.pop("MANPAGER", None)
        try:
            pydoc.pager(content)
        finally:
            if saved is not None:
                os.environ["MANPAGER"] = saved


class _NovelConsole(Console):
    """默认使用 :class:`_NovelPager` 的 Console 子类。

    Attributes:
        无显式属性，继承自 Console。
    """

    def pager(
        self,
        pager: Pager | None = None,
        styles: bool = False,
        links: bool = False,
    ) -> PagerContext:
        """获取 pager 上下文。

        Args:
            pager: Pager 实例，若为 None 则使用 _NovelPager。
            styles: 是否保留样式。
            links: 是否保留链接。

        Returns:
            PagerContext 上下文对象。
        """
        if pager is None:
            pager = _NovelPager()
        return super().pager(pager=pager, styles=styles, links=links)


console = _NovelConsole(highlight=False, theme=NEUTRAL_MARKDOWN_THEME)


# 匹配 Rich Style(link=...) 输出的 OSC 8 超链接开/闭标记。
# 格式：ESC ] 8 ; <params> ; <uri> ST，其中 ST 为 ESC \ 或 BEL (\x07)。
# prompt_toolkit 的 ANSI 解析器不理解 OSC 8，会将原始转义字节渲染为可见乱码
#（如 "8;id=391551;https://…"）。我们将每个标记包裹在 \001…\002 中，
# 使 prompt_toolkit 将其视为 ZeroWidthEscape 并通过 write_raw 传递给终端，
# 从而保留可点击链接。
_OSC8_RE = re.compile(r"\x1b\]8;[^\x07\x1b]*(?:\x1b\\|\x07)")


def _wrap_osc8_as_zero_width(m: re.Match[str]) -> str:
    """将 OSC 8 标记包裹在 \\001…\\002 中以用于 prompt_toolkit ZeroWidthEscape。

    Args:
        m: 正则匹配对象。

    Returns:
        包裹后的字符串。
    """
    return f"\x01{m.group(0)}\x02"


def render_to_ansi(renderable: RenderableType, *, columns: int) -> str:
    """将 Rich 可渲染对象转换为 ANSI 字符串以用于 prompt_toolkit 集成。

    Args:
        renderable: Rich 可渲染对象。
        columns: 输出列数（宽度）。

    Returns:
        ANSI 格式的字符串。
    """
    from io import StringIO

    width = max(20, columns)
    buf = StringIO()
    temp = Console(
        file=buf,
        force_terminal=True,
        width=width,
        theme=NEUTRAL_MARKDOWN_THEME,
        highlight=False,
    )
    temp.print(renderable, end="")
    result = buf.getvalue()
    return _OSC8_RE.sub(_wrap_osc8_as_zero_width, result)
