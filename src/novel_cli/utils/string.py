"""字符串处理工具。

本模块提供字符串截断、缩写和随机生成等功能。
"""

from __future__ import annotations

import random
import re
import string

_NEWLINE_RE = re.compile(r"[\r\n]+")


def shorten(text: str, *, width: int, placeholder: str = "…") -> str:
    """将文本截断到指定宽度。

    首先规范化空白字符，然后截断——优先在单词边界处截断，
    但如果没有合适的空格（如中日韩文本），则进行硬截断以确保
    文本不会仅剩占位符。

    Args:
        text: 要截断的文本。
        width: 最大宽度（字符数）。
        placeholder: 截断时使用的占位符，默认为省略号。

    Returns:
        截断后的文本字符串。
    """
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    cut = width - len(placeholder)
    if cut <= 0:
        return text[:width]
    space = text.rfind(" ", 0, cut + 1)
    if space > 0:
        cut = space
    return text[:cut].rstrip() + placeholder


def shorten_middle(text: str, width: int, remove_newline: bool = True) -> str:
    """在中间插入省略号来截断文本。

    Args:
        text: 要截断的文本。
        width: 最大宽度（字符数）。
        remove_newline: 是否移除换行符，默认为 True。

    Returns:
        截断后的文本字符串。
    """
    if len(text) <= width:
        return text
    if remove_newline:
        text = _NEWLINE_RE.sub(" ", text)
    return text[: width // 2] + "..." + text[-width // 2 :]


def random_string(length: int = 8) -> str:
    """生成指定长度的随机字符串。

    Args:
        length: 字符串长度，默认为 8。

    Returns:
        由小写字母组成的随机字符串。
    """
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for _ in range(length))