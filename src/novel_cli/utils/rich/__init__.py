"""项目全局 Rich 配置工具。

本模块提供 Rich 库的包装配置功能，支持在字符级别和单词级别之间切换换行模式。
"""

from __future__ import annotations

import re
from typing import Final

from rich import _wrap

# Rich 用于计算换行时机时的默认正则表达式
_DEFAULT_WRAP_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s*\S+\s*")
_CHAR_WRAP_PATTERN: Final[re.Pattern[str]] = re.compile(r".", re.DOTALL)


def enable_character_wrap() -> None:
    """将 Rich 的换行逻辑切换为逐字符换行。

    Rich 默认尝试保持单词完整；我们通过覆盖内部正则表达式，
    使 Markdown 渲染可以在超出终端宽度时在任意列换行。
    """

    _wrap.re_word = _CHAR_WRAP_PATTERN


def restore_word_wrap() -> None:
    """恢复 Rich 默认的单词换行模式。"""

    _wrap.re_word = _DEFAULT_WRAP_PATTERN


# 全局启用字符级换行以支持 CLI
enable_character_wrap()