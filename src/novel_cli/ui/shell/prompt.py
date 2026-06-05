"""提示词输入模块。

本模块实现了 CLI Shell 的提示词输入功能，包括：
- 斜杠命令补全器（SlashCommandCompleter）
- 本地文件提及补全器（LocalFileMentionCompleter）
- 斜杠命令菜单控件（SlashCommandMenuControl）
- 用户输入模型（UserInput）
- Git 状态显示功能
- 提示词 UI 状态管理
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import shlex
import subprocess
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from hashlib import md5
from pathlib import Path
from typing import Any, Literal, Protocol, cast, override

from kaos.path import KaosPath
from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.clipboard.pyperclip import PyperclipClipboard
from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    FuzzyCompleter,
    WordCompleter,
    merge_completers,
)
from prompt_toolkit.data_structures import Point
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_completions, has_focus, is_done
from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText, to_formatted_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    DynamicContainer,
    Float,
    FloatContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, UIContent, UIControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.utils import get_cwidth
from pydantic import BaseModel, ValidationError

from novel_cli.llm import ModelCapability
from novel_cli.share import get_share_dir
from novel_cli.soul import StatusSnapshot, format_context_status
from novel_cli.ui.shell import placeholders as prompt_placeholders
from novel_cli.ui.shell.console import console
from novel_cli.ui.shell.placeholders import (
    PromptPlaceholderManager,
    normalize_pasted_text,
    sanitize_surrogates,
)
from novel_cli.ui.theme import get_prompt_style, get_toolbar_colors
from novel_cli.utils.clipboard import (
    grab_media_from_clipboard,
    is_clipboard_available,
)
from novel_cli.utils.logging import logger
from novel_cli.utils.slashcmd import SlashCommand
from novel_cli.wire.types import ContentPart

AttachmentCache = prompt_placeholders.AttachmentCache
CachedAttachment = prompt_placeholders.CachedAttachment
_parse_attachment_kind = prompt_placeholders.parse_attachment_kind

PROMPT_SYMBOL = "✨"
PROMPT_SYMBOL_SHELL = "$"
PROMPT_SYMBOL_THINKING = "💫"
PROMPT_SYMBOL_PLAN = "📋"


class SlashCommandCompleter(Completer):
    """斜杠命令补全器。

    提供斜杠命令的补全功能，具有以下特性：
    - 每个斜杠命令显示为一行，使用规范的 "/name" 格式
    - 支持通过主名称或任意别名进行模糊匹配，并插入规范的 "/name"
    - 仅在当前标记以 '/' 开头时激活

    Attributes:
        _available_commands: 可用的斜杠命令列表。
        _command_lookup: 命令名称到命令对象的映射字典。
        _word_pattern: 匹配单词的正则表达式。
        _fuzzy_pattern: 模糊匹配的正则表达式模式。
        _word_completer: 单词补全器。
        _fuzzy: 模糊补全器。
    """

    def __init__(self, available_commands: Sequence[SlashCommand[Any]]) -> None:
        """初始化斜杠命令补全器。

        Args:
            available_commands: 可用的斜杠命令序列。
        """
        super().__init__()
        self._available_commands = list(available_commands)
        self._command_lookup: dict[str, list[SlashCommand[Any]]] = {}
        words: list[str] = []

        for cmd in sorted(self._available_commands, key=lambda c: c.name):
            if cmd.name not in self._command_lookup:
                self._command_lookup[cmd.name] = []
                words.append(cmd.name)
            self._command_lookup[cmd.name].append(cmd)
            for alias in cmd.aliases:
                if alias in self._command_lookup:
                    self._command_lookup[alias].append(cmd)
                else:
                    self._command_lookup[alias] = [cmd]
                    words.append(alias)

        self._word_pattern = re.compile(r"[^\s]+")
        self._fuzzy_pattern = r"^[^\s]*"
        self._word_completer = WordCompleter(words, WORD=False, pattern=self._word_pattern)
        self._fuzzy = FuzzyCompleter(self._word_completer, WORD=False, pattern=self._fuzzy_pattern)

    @staticmethod
    def should_complete(document: Document) -> bool:
        """判断当前缓冲区是否应激活斜杠命令补全。

        Args:
            document: 当前文档对象。

        Returns:
            如果应激活补全则返回 True，否则返回 False。
        """
        text = document.text_before_cursor

        if document.text_after_cursor.strip():
            return False

        last_space = text.rfind(" ")
        token = text[last_space + 1 :]
        prefix = text[: last_space + 1] if last_space != -1 else ""

        return not prefix.strip() and token.startswith("/")

    @override
    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """获取补全候选列表。

        Args:
            document: 当前文档对象。
            complete_event: 补全事件对象。

        Yields:
            补全候选对象。
        """
        if not self.should_complete(document):
            return
        text = document.text_before_cursor
        last_space = text.rfind(" ")
        token = text[last_space + 1 :]

        typed = token[1:]
        if typed and typed in self._command_lookup:
            return
        mention_doc = Document(text=typed, cursor_position=len(typed))
        candidates = list(self._fuzzy.get_completions(mention_doc, complete_event))

        seen: set[str] = set()

        for candidate in candidates:
            commands = self._command_lookup.get(candidate.text)
            if not commands:
                continue
            for cmd in commands:
                if cmd.name in seen:
                    continue
                seen.add(cmd.name)
                yield Completion(
                    text=f"/{cmd.name}",
                    start_position=-len(token),
                    display=f"/{cmd.name}",
                    display_meta=cmd.description,
                )


def _truncate_to_width(text: str, width: int) -> str:
    """将文本截断到指定宽度。

    如果文本宽度超过指定宽度，则在末尾添加省略号。
    如果文本宽度小于指定宽度，则用空格填充。

    Args:
        text: 待截断的文本。
        width: 目标宽度。

    Returns:
        截断后的文本，长度等于指定宽度。
    """
    if width <= 0:
        return ""

    total = 0
    chars: list[str] = []
    for ch in text:
        ch_width = get_cwidth(ch)
        if total + ch_width > width:
            break
        chars.append(ch)
        total += ch_width

    if total == get_cwidth(text):
        return text + (" " * max(0, width - total))

    ellipsis = "..."
    ellipsis_width = get_cwidth(ellipsis)
    if width <= ellipsis_width:
        return "." * width

    available = width - ellipsis_width
    total = 0
    chars = []
    for ch in text:
        ch_width = get_cwidth(ch)
        if total + ch_width > available:
            break
        chars.append(ch)
        total += ch_width
    return "".join(chars) + ellipsis + (" " * max(0, width - total - ellipsis_width))


def _wrap_to_width(text: str, width: int, *, max_lines: int | None = None) -> list[str]:
    """将文本换行到指定宽度。

    Args:
        text: 待换行的文本。
        width: 目标宽度。
        max_lines: 最大行数限制，如果指定则在最后一行截断多余内容。

    Returns:
        换行后的文本行列表。
    """
    if width <= 0:
        return []

    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current_words: list[str] = []
    current_width = 0
    index = 0

    while index < len(words):
        word = words[index]
        word_width = get_cwidth(word)
        separator_width = 1 if current_words else 0

        if current_words and current_width + separator_width + word_width <= width:
            current_words.append(word)
            current_width += separator_width + word_width
            index += 1
            continue

        if not current_words and word_width <= width:
            current_words.append(word)
            current_width = word_width
            index += 1
            continue

        if not current_words and word_width > width:
            current_words.append(_truncate_to_width(word, width).rstrip())
            current_width = get_cwidth(current_words[0])
            index += 1

        lines.append(" ".join(current_words))
        current_words = []
        current_width = 0

        if max_lines is not None and len(lines) == max_lines:
            remaining = " ".join(words[index:])
            if remaining:
                prefix = f"{lines[-1]} " if lines[-1] else ""
                lines[-1] = _truncate_to_width(prefix + remaining, width).rstrip()
            return lines

    if current_words:
        line = " ".join(current_words)
        if max_lines is not None and len(lines) + 1 > max_lines:
            if lines:
                lines[-1] = _truncate_to_width(f"{lines[-1]} {line}", width).rstrip()
            else:
                lines.append(_truncate_to_width(line, width).rstrip())
        else:
            lines.append(line)

    return lines


def _find_prompt_float_container(layout_container: object) -> FloatContainer | None:
    """在布局容器中查找浮动容器。

    Args:
        layout_container: 布局容器对象。

    Returns:
        找到的 FloatContainer 对象，如果未找到则返回 None。
    """
    if not isinstance(layout_container, HSplit):
        return None

    for child in cast(Sequence[object], layout_container.children):
        float_container = _extract_float_container(child)
        if float_container is not None:
            return float_container
    return None


def _extract_float_container(container: object) -> FloatContainer | None:
    """从容器中提取浮动容器。

    Args:
        container: 容器对象。

    Returns:
        提取到的 FloatContainer 对象，如果未找到则返回 None。
    """
    if isinstance(container, FloatContainer):
        return container
    if isinstance(container, ConditionalContainer):
        if isinstance(container.content, FloatContainer):
            return container.content
        if isinstance(container.alternative_content, FloatContainer):
            return container.alternative_content
    return None


def _find_default_buffer_container(
    layout_container: object,
    target_buffer: Buffer,
) -> ConditionalContainer | None:
    """在布局容器中查找目标缓冲区的条件容器。

    Args:
        layout_container: 布局容器对象。
        target_buffer: 目标缓冲区对象。

    Returns:
        找到的 ConditionalContainer 对象，如果未找到则返回 None。
    """
    seen: set[int] = set()

    def _walk(node: object) -> ConditionalContainer | None:
        """递归遍历节点查找条件容器。

        Args:
            node: 当前遍历的节点对象。

        Returns:
            找到的 ConditionalContainer 对象，如果未找到则返回 None。
        """
        if id(node) in seen:
            return None
        seen.add(id(node))

        if isinstance(node, ConditionalContainer):
            content = getattr(node, "content", None)
            if isinstance(content, Window):
                control = content.content
                if isinstance(control, BufferControl) and control.buffer is target_buffer:
                    return node

        if isinstance(node, DynamicContainer):
            with contextlib.suppress(Exception):
                found = _walk(node.get_container())
                if found is not None:
                    return found

        for attr in ("children", "content", "floats", "container"):
            if not hasattr(node, attr):
                continue
            value = getattr(node, attr)
            if attr == "children" and isinstance(value, Sequence):
                for child in value:  # pyright: ignore[reportUnknownVariableType]
                    found = _walk(child)  # pyright: ignore[reportUnknownArgumentType]
                    if found is not None:
                        return found
            elif attr == "floats" and isinstance(value, Sequence):
                for float_ in value:  # pyright: ignore[reportUnknownVariableType]
                    content = getattr(float_, "content", None)  # pyright: ignore[reportUnknownArgumentType]
                    if content is None:
                        continue
                    found = _walk(content)
                    if found is not None:
                        return found
            elif (
                attr in {"content", "container"}
                and value is not None
                and type(value).__module__.startswith("prompt_toolkit")
            ):
                found = _walk(value)
                if found is not None:
                    return found
        return None

    return _walk(layout_container)


class SlashCommandMenuControl(UIControl):
    """斜杠命令菜单控件。

    将斜杠命令补全项渲染为与 Shell UI 匹配的全宽菜单。

    Attributes:
        _MAX_EXPANDED_META_LINES: 选中项元数据展开后的最大行数。
        _left_padding: 左侧填充宽度的回调函数。
        _scroll_offset: 滚动偏移量。
    """

    _MAX_EXPANDED_META_LINES = 3

    def __init__(
        self,
        *,
        left_padding: Callable[[], int],
        scroll_offset: int = 1,
    ) -> None:
        """初始化斜杠命令菜单控件。

        Args:
            left_padding: 返回左侧填充宽度的回调函数。
            scroll_offset: 滚动偏移量，默认为 1。
        """
        self._left_padding = left_padding
        self._scroll_offset = scroll_offset

    def has_focus(self) -> bool:
        """检查控件是否获得焦点。

        Returns:
            总是返回 False，因为菜单控件不需要焦点。
        """
        return False

    def preferred_width(self, max_available_width: int) -> int | None:
        """获取控件的首选宽度。

        Args:
            max_available_width: 最大可用宽度。

        Returns:
            控件的首选宽度，等于最大可用宽度。
        """
        return max_available_width

    def preferred_height(
        self,
        width: int,
        max_available_height: int,
        wrap_lines: bool,
        get_line_prefix: Callable[..., AnyFormattedText] | None,
    ) -> int | None:
        """获取控件的首选高度。

        Args:
            width: 可用宽度。
            max_available_height: 最大可用高度。
            wrap_lines: 是否换行。
            get_line_prefix: 获取行前缀的回调函数。

        Returns:
            控件的首选高度。
        """
        app = get_app_or_none()
        complete_state = (
            getattr(app.current_buffer, "complete_state", None) if app is not None else None
        )
        if complete_state is None:
            return 0
        completions = complete_state.completions
        selected_index = complete_state.complete_index
        if selected_index is None:
            return min(max_available_height, len(completions) + 1)
        menu_width = max(0, width - self._left_padding())
        marker_width = 2
        command_width = self._command_column_width(completions, menu_width, marker_width)
        gap_width = 3 if menu_width > command_width + 6 else 1
        meta_width = max(0, menu_width - marker_width - command_width - gap_width)
        selected_meta_lines = self._selected_meta_lines(
            completions[selected_index].display_meta_text,
            meta_width,
        )
        return min(max_available_height, len(completions) + len(selected_meta_lines))

    def create_content(self, width: int, height: int) -> UIContent:
        """创建控件内容。

        Args:
            width: 可用宽度。
            height: 可用高度。

        Returns:
            渲染后的 UI 内容对象。
        """
        app = get_app_or_none()
        complete_state = (
            getattr(app.current_buffer, "complete_state", None) if app is not None else None
        )
        if complete_state is None or not complete_state.completions:
            return UIContent()

        completions = complete_state.completions
        selected_index = complete_state.complete_index
        available_rows = max(1, height - 1)

        menu_width = max(0, width - self._left_padding())
        marker_width = 2
        command_width = self._command_column_width(completions, menu_width, marker_width)
        gap_width = 3 if menu_width > command_width + 6 else 1
        meta_width = max(0, menu_width - marker_width - command_width - gap_width)

        rendered_lines: list[FormattedText] = [
            FormattedText([("class:slash-completion-menu.separator", "─" * max(0, width))])
        ]
        selected_line_index = 0

        if selected_index is None:
            end = min(len(completions) - 1, available_rows - 1)
            for index in range(0, end + 1):
                rendered_lines.append(
                    self._render_single_line_item(
                        width=width,
                        completion=completions[index],
                        marker_width=marker_width,
                        command_width=command_width,
                        meta_width=meta_width,
                        gap_width=gap_width,
                        is_current=False,
                    )
                )

            return UIContent(
                get_line=lambda i: rendered_lines[i],
                line_count=len(rendered_lines),
                cursor_position=Point(x=0, y=selected_line_index),
            )

        selected_meta_lines = self._selected_meta_lines(
            completions[selected_index].display_meta_text,
            meta_width,
        )
        start, end = self._visible_window_bounds(
            completion_count=len(completions),
            selected_index=selected_index,
            available_rows=available_rows,
            selected_item_height=len(selected_meta_lines),
        )
        selected_line_index = 1

        for index in range(start, end + 1):
            completion = completions[index]
            if index == selected_index:
                selected_line_index = len(rendered_lines)
                rendered_lines.extend(
                    self._render_selected_item_lines(
                        width=width,
                        completion=completion,
                        marker_width=marker_width,
                        command_width=command_width,
                        meta_width=meta_width,
                        gap_width=gap_width,
                        meta_lines=selected_meta_lines,
                    )
                )
                continue

            rendered_lines.append(
                self._render_single_line_item(
                    width=width,
                    completion=completion,
                    marker_width=marker_width,
                    command_width=command_width,
                    meta_width=meta_width,
                    gap_width=gap_width,
                    is_current=False,
                )
            )

        return UIContent(
            get_line=lambda i: rendered_lines[i],
            line_count=len(rendered_lines),
            cursor_position=Point(x=0, y=selected_line_index),
        )

    def _selected_meta_lines(self, text: str, meta_width: int) -> list[str]:
        """获取选中项的元数据行列表。

        Args:
            text: 元数据文本。
            meta_width: 元数据显示宽度。

        Returns:
            换行后的元数据行列表。
        """
        lines = _wrap_to_width(
            text,
            meta_width,
            max_lines=self._MAX_EXPANDED_META_LINES,
        )
        return lines or [""]

    def _visible_window_bounds(
        self,
        *,
        completion_count: int,
        selected_index: int,
        available_rows: int,
        selected_item_height: int,
    ) -> tuple[int, int]:
        """计算可见窗口的边界索引。

        Args:
            completion_count: 补全项总数。
            selected_index: 当前选中项索引。
            available_rows: 可用行数。
            selected_item_height: 选中项高度。

        Returns:
            包含起始索引和结束索引的元组。
        """
        selected_item_height = min(selected_item_height, available_rows)
        remaining_rows = max(0, available_rows - selected_item_height)

        before = min(self._scroll_offset, selected_index, remaining_rows)
        remaining_rows -= before
        after = min(completion_count - selected_index - 1, remaining_rows)
        remaining_rows -= after

        extra_before = min(selected_index - before, remaining_rows)
        before += extra_before
        remaining_rows -= extra_before

        extra_after = min(completion_count - selected_index - 1 - after, remaining_rows)
        after += extra_after

        return selected_index - before, selected_index + after

    def _command_column_width(
        self,
        completions: Sequence[Completion],
        menu_width: int,
        marker_width: int,
    ) -> int:
        """计算命令列的宽度。

        Args:
            completions: 补全项序列。
            menu_width: 菜单总宽度。
            marker_width: 标记宽度。

        Returns:
            命令列的宽度。
        """
        if menu_width <= 0:
            return 0
        longest = max((get_cwidth(c.display_text) for c in completions), default=0)
        preferred = longest + 2
        usable_width = max(0, menu_width - marker_width)
        minimum = min(usable_width, 18)
        maximum = max(minimum, min(28, usable_width // 2))
        return max(minimum, min(preferred, maximum))

    def _render_single_line_item(
        self,
        *,
        width: int,
        completion: Completion,
        marker_width: int,
        command_width: int,
        meta_width: int,
        gap_width: int,
        is_current: bool,
    ) -> FormattedText:
        """渲染单行补全项。

        Args:
            width: 总宽度。
            completion: 补全项对象。
            marker_width: 标记宽度。
            command_width: 命令列宽度。
            meta_width: 元数据列宽度。
            gap_width: 间隙宽度。
            is_current: 是否为当前选中项。

        Returns:
            格式化文本片段列表。
        """
        padding_width = max(0, width - marker_width - command_width - meta_width - gap_width)
        left_padding = min(self._left_padding(), padding_width)
        trailing_width = max(
            0,
            width - left_padding - marker_width - command_width - gap_width - meta_width,
        )

        command_style = (
            "class:slash-completion-menu.command.current"
            if is_current
            else "class:slash-completion-menu.command"
        )
        meta_style = (
            "class:slash-completion-menu.meta.current"
            if is_current
            else "class:slash-completion-menu.meta"
        )
        marker_style = (
            "class:slash-completion-menu.marker.current"
            if is_current
            else "class:slash-completion-menu.marker"
        )
        marker = "› " if is_current else "  "

        fragments: FormattedText = FormattedText()
        fragments.append(("class:slash-completion-menu", " " * left_padding))
        fragments.append((marker_style, marker.ljust(marker_width)))
        fragments.append(
            (command_style, _truncate_to_width(completion.display_text, command_width))
        )
        fragments.append(("class:slash-completion-menu", " " * gap_width))
        fragments.append((meta_style, _truncate_to_width(completion.display_meta_text, meta_width)))
        fragments.append(("class:slash-completion-menu", " " * trailing_width))
        return fragments

    def _render_selected_item_lines(
        self,
        *,
        width: int,
        completion: Completion,
        marker_width: int,
        command_width: int,
        meta_width: int,
        gap_width: int,
        meta_lines: Sequence[str],
    ) -> list[FormattedText]:
        """渲染选中项的多行内容。

        Args:
            width: 总宽度。
            completion: 补全项对象。
            marker_width: 标记宽度。
            command_width: 命令列宽度。
            meta_width: 元数据列宽度。
            gap_width: 间隙宽度。
            meta_lines: 元数据行列表。

        Returns:
            格式化文本行列表。
        """
        lines = [
            self._render_single_line_item(
                width=width,
                completion=Completion(
                    text=completion.text,
                    start_position=completion.start_position,
                    display=completion.display,
                    display_meta=meta_lines[0],
                ),
                marker_width=marker_width,
                command_width=command_width,
                meta_width=meta_width,
                gap_width=gap_width,
                is_current=True,
            )
        ]

        continuation_prefix = (
            " " * self._left_padding() + " " * marker_width + " " * command_width + " " * gap_width
        )
        continuation_trailing = max(
            0,
            width - get_cwidth(continuation_prefix) - meta_width,
        )
        for meta_line in meta_lines[1:]:
            fragments: FormattedText = FormattedText()
            fragments.append(("class:slash-completion-menu", continuation_prefix))
            fragments.append(
                (
                    "class:slash-completion-menu.meta.current",
                    _truncate_to_width(meta_line, meta_width),
                )
            )
            fragments.append(("class:slash-completion-menu", " " * continuation_trailing))
            lines.append(fragments)

        return lines


class LocalFileMentionCompleter(Completer):
    """本地文件提及补全器。

    通过索引工作区文件提供 `@` 路径模糊补全功能。

    Attributes:
        _FRAGMENT_PATTERN: 匹配片段的正则表达式模式。
        _TRIGGER_GUARDS: 触发保护字符集合。
        _IGNORED_NAME_GROUPS: 忽略的名称分组字典。
        _IGNORED_NAMES: 所有忽略名称的集合。
        _IGNORED_PATTERN_PARTS: 忽略模式的部分列表。
        _IGNORED_PATTERNS: 编译后的忽略正则表达式模式。
        _root: 工作区根路径。
        _refresh_interval: 刷新间隔（秒）。
        _limit: 路径数量限制。
        _cache_time: 深度路径缓存时间戳。
        _cached_paths: 缓存的深度路径列表。
        _top_cache_time: 顶层路径缓存时间戳。
        _top_cached_paths: 缓存的顶层路径列表。
        _fragment_hint: 当前片段提示。
        _word_completer: 单词补全器。
        _fuzzy: 模糊补全器。
    """

    _FRAGMENT_PATTERN = re.compile(r"[^\s@]+")
    _TRIGGER_GUARDS = frozenset((".", "-", "_", "`", "'", '"', ":", "@", "#", "~"))
    _IGNORED_NAME_GROUPS: dict[str, tuple[str, ...]] = {
        "vcs_metadata": (".DS_Store", ".bzr", ".git", ".hg", ".svn"),
        "tooling_caches": (
            ".build",
            ".cache",
            ".coverage",
            ".fleet",
            ".gradle",
            ".idea",
            ".ipynb_checkpoints",
            ".pnpm-store",
            ".pytest_cache",
            ".pub-cache",
            ".ruff_cache",
            ".swiftpm",
            ".tox",
            ".venv",
            ".vs",
            ".vscode",
            ".yarn",
            ".yarn-cache",
        ),
        "js_frontend": (
            ".next",
            ".nuxt",
            ".parcel-cache",
            ".svelte-kit",
            ".turbo",
            ".vercel",
            "node_modules",
        ),
        "python_packaging": (
            "__pycache__",
            "build",
            "coverage",
            "dist",
            "htmlcov",
            "pip-wheel-metadata",
            "venv",
        ),
        "java_jvm": (".mvn", "out", "target"),
        "dotnet_native": ("bin", "cmake-build-debug", "cmake-build-release", "obj"),
        "bazel_buck": ("bazel-bin", "bazel-out", "bazel-testlogs", "buck-out"),
        "misc_artifacts": (
            ".dart_tool",
            ".serverless",
            ".stack-work",
            ".terraform",
            ".terragrunt-cache",
            "DerivedData",
            "Pods",
            "deps",
            "tmp",
            "vendor",
        ),
    }
    _IGNORED_NAMES = frozenset(name for group in _IGNORED_NAME_GROUPS.values() for name in group)
    _IGNORED_PATTERN_PARTS: tuple[str, ...] = (
        r".*_cache$",
        r".*-cache$",
        r".*\.egg-info$",
        r".*\.dist-info$",
        r".*\.py[co]$",
        r".*\.class$",
        r".*\.sw[po]$",
        r".*~$",
        r".*\.(?:tmp|bak)$",
    )
    _IGNORED_PATTERNS = re.compile(
        "|".join(f"(?:{part})" for part in _IGNORED_PATTERN_PARTS),
        re.IGNORECASE,
    )

    def __init__(
        self,
        root: Path,
        *,
        refresh_interval: float = 2.0,
        limit: int = 1000,
    ) -> None:
        """初始化本地文件提及补全器。

        Args:
            root: 工作区根路径。
            refresh_interval: 缓存刷新间隔（秒），默认为 2.0。
            limit: 路径数量限制，默认为 1000。
        """
        self._root = root
        self._refresh_interval = refresh_interval
        self._limit = limit
        self._cache_time: float = 0.0
        self._cached_paths: list[str] = []
        self._top_cache_time: float = 0.0
        self._top_cached_paths: list[str] = []
        self._fragment_hint: str | None = None

        self._word_completer = WordCompleter(
            self._get_paths,
            WORD=False,
            pattern=self._FRAGMENT_PATTERN,
        )

        self._fuzzy = FuzzyCompleter(
            self._word_completer,
            WORD=False,
            pattern=r"^[^\s@]*",
        )

    @classmethod
    def _is_ignored(cls, name: str) -> bool:
        """检查名称是否应被忽略。

        Args:
            name: 待检查的名称。

        Returns:
            如果应被忽略则返回 True，否则返回 False。
        """
        if not name:
            return True
        if name in cls._IGNORED_NAMES:
            return True
        return bool(cls._IGNORED_PATTERNS.fullmatch(name))

    def _get_paths(self) -> list[str]:
        """获取路径列表。

        根据当前片段提示决定返回顶层路径还是深度路径。

        Returns:
            路径字符串列表。
        """
        fragment = self._fragment_hint or ""
        if "/" not in fragment and len(fragment) < 3:
            return self._get_top_level_paths()
        return self._get_deep_paths()

    def _get_top_level_paths(self) -> list[str]:
        """获取顶层路径列表。

        Returns:
            顶层路径字符串列表。
        """
        now = time.monotonic()
        if now - self._top_cache_time <= self._refresh_interval:
            return self._top_cached_paths

        entries: list[str] = []
        try:
            for entry in sorted(self._root.iterdir(), key=lambda p: p.name):
                name = entry.name
                if self._is_ignored(name):
                    continue
                entries.append(f"{name}/" if entry.is_dir() else name)
                if len(entries) >= self._limit:
                    break
        except OSError:
            return self._top_cached_paths

        self._top_cached_paths = entries
        self._top_cache_time = now
        return self._top_cached_paths

    def _get_deep_paths(self) -> list[str]:
        """获取深度路径列表。

        递归遍历工作区目录，返回所有非忽略的路径。

        Returns:
            深度路径字符串列表。
        """
        now = time.monotonic()
        if now - self._cache_time <= self._refresh_interval:
            return self._cached_paths

        paths: list[str] = []
        try:
            for current_root, dirs, files in os.walk(self._root):
                relative_root = Path(current_root).relative_to(self._root)

                # 防止进入被忽略的目录
                dirs[:] = sorted(d for d in dirs if not self._is_ignored(d))

                if relative_root.parts and any(
                    self._is_ignored(part) for part in relative_root.parts
                ):
                    dirs[:] = []
                    continue

                if relative_root.parts:
                    paths.append(relative_root.as_posix() + "/")
                    if len(paths) >= self._limit:
                        break

                for file_name in sorted(files):
                    if self._is_ignored(file_name):
                        continue
                    relative = (relative_root / file_name).as_posix()
                    if not relative:
                        continue
                    paths.append(relative)
                    if len(paths) >= self._limit:
                        break

                if len(paths) >= self._limit:
                    break
        except OSError:
            return self._cached_paths

        self._cached_paths = paths
        self._cache_time = now
        return self._cached_paths

    @staticmethod
    def _extract_fragment(text: str) -> str | None:
        """从文本中提取 `@` 后的片段。

        Args:
            text: 待提取的文本。

        Returns:
            提取到的片段，如果不存在则返回 None。
        """
        index = text.rfind("@")
        if index == -1:
            return None

        if index > 0:
            prev = text[index - 1]
            if prev.isalnum() or prev in LocalFileMentionCompleter._TRIGGER_GUARDS:
                return None

        fragment = text[index + 1 :]
        if not fragment:
            return ""

        if any(ch.isspace() for ch in fragment):
            return None

        return fragment

    def _is_completed_file(self, fragment: str) -> bool:
        """检查片段是否指向已存在的文件。

        Args:
            fragment: 待检查的路径片段。

        Returns:
            如果指向已存在的文件则返回 True，否则返回 False。
        """
        candidate = fragment.rstrip("/")
        if not candidate:
            return False
        try:
            return (self._root / candidate).is_file()
        except OSError:
            return False

    @override
    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """获取补全候选列表。

        Args:
            document: 当前文档对象。
            complete_event: 补全事件对象。

        Yields:
            补全候选对象。
        """
        fragment = self._extract_fragment(document.text_before_cursor)
        if fragment is None:
            return
        if self._is_completed_file(fragment):
            return

        mention_doc = Document(text=fragment, cursor_position=len(fragment))
        self._fragment_hint = fragment
        try:
            # 首先，向模糊补全器请求候选
            candidates = list(self._fuzzy.get_completions(mention_doc, complete_event))

            # 重新排序：优先匹配文件名
            frag_lower = fragment.lower()

            def _rank(c: Completion) -> tuple[int, ...]:
                """对补全候选进行排序。

                Args:
                    c: 补全候选对象。

                Returns:
                    排序键元组。
                """
                path = c.text
                base = path.rstrip("/").split("/")[-1].lower()
                if base.startswith(frag_lower):
                    cat = 0
                elif frag_lower in base:
                    cat = 1
                else:
                    cat = 2
                # 在同一类别中保持原始 FuzzyCompleter 的顺序
                return (cat,)

            candidates.sort(key=_rank)
            yield from candidates
        finally:
            self._fragment_hint = None


class _HistoryEntry(BaseModel):
    """历史记录条目模型。

    Attributes:
        content: 条目内容字符串。
    """

    content: str


def _load_history_entries(history_file: Path) -> list[_HistoryEntry]:
    """加载历史记录条目列表。

    Args:
        history_file: 历史记录文件路径。

    Returns:
        历史记录条目列表。
    """
    entries: list[_HistoryEntry] = []
    if not history_file.exists():
        return entries

    try:
        with history_file.open(encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "Failed to parse user history line; skipping: {line}",
                        line=line,
                    )
                    continue
                try:
                    entry = _HistoryEntry.model_validate(record)
                    entries.append(entry)
                except ValidationError:
                    logger.warning(
                        "Failed to validate user history entry; skipping: {line}",
                        line=line,
                    )
                    continue
    except OSError as exc:
        logger.warning(
            "Failed to load user history file: {file} ({error})",
            file=history_file,
            error=exc,
        )

    return entries


class PromptMode(Enum):
    """提示词模式枚举。

    定义输入模式类型。

    Attributes:
        AGENT: 智能体模式。
        SHELL: Shell 模式。
    """

    AGENT = "agent"
    SHELL = "shell"

    def toggle(self) -> PromptMode:
        """切换提示词模式。

        Returns:
            切换后的新模式。
        """
        return PromptMode.SHELL if self == PromptMode.AGENT else PromptMode.AGENT

    def __str__(self) -> str:
        """返回模式的字符串表示。

        Returns:
            模式值的字符串。
        """
        return self.value


class PromptUIState(Enum):
    """提示词 UI 状态枚举。

    定义输入界面的状态类型。

    Attributes:
        NORMAL_INPUT: 正常输入状态。
        MODAL_HIDDEN_INPUT: 模态隐藏输入状态。
        MODAL_TEXT_INPUT: 模态文本输入状态。
    """

    NORMAL_INPUT = "normal_input"
    MODAL_HIDDEN_INPUT = "modal_hidden_input"
    MODAL_TEXT_INPUT = "modal_text_input"


class UserInput(BaseModel):
    """用户输入模型。

    表示用户的输入内容，包含模式、命令文本和富文本内容。

    Attributes:
        mode: 输入模式。
        command: 用户输入的纯文本表示。
        resolved_command: UI 占位符展开后的文本命令。
        content: 富文本内容部分列表。
    """

    mode: PromptMode
    command: str
    """用户输入的纯文本表示。"""
    resolved_command: str
    """UI 占位符展开后的文本命令。"""
    content: list[ContentPart]
    """富文本内容部分列表。"""

    def __str__(self) -> str:
        """返回用户输入的字符串表示。

        Returns:
            原始命令文本。
        """
        return self.command

    def __bool__(self) -> bool:
        """判断用户输入是否非空。

        Returns:
            如果命令非空则返回 True，否则返回 False。
        """
        return bool(self.command)


_IDLE_REFRESH_INTERVAL = 1.0
_RUNNING_REFRESH_INTERVAL = 0.1

_GIT_BRANCH_TTL = 5.0
_GIT_STATUS_TTL = 15.0
_TIP_ROTATE_INTERVAL = 30.0
_MAX_CWD_COLS = 30
_MAX_BRANCH_COLS = 22


@dataclass
class _GitBranchState:
    """Git 分支状态数据类。

    Attributes:
        timestamp: 状态更新时间戳。
        branch: 当前分支名称。
        proc: 运行中的子进程对象。
    """

    timestamp: float = 0.0
    branch: str | None = None
    proc: subprocess.Popen[str] | None = None


@dataclass
class _GitStatusState:
    """Git 状态数据类。

    Attributes:
        timestamp: 状态更新时间戳。
        dirty: 是否有未提交的变更。
        ahead: 领先远程的提交数。
        behind: 落后远程的提交数。
        proc: 运行中的子进程对象。
    """

    timestamp: float = 0.0
    dirty: bool = False
    ahead: int = 0
    behind: int = 0
    proc: subprocess.Popen[str] | None = None


_git_branch_state = _GitBranchState()
_git_status_state = _GitStatusState()

_GIT_STATUS_AB_RE = re.compile(r"\[(?:ahead (\d+))?(?:, )?(?:behind (\d+))?\]")


def _get_git_branch() -> str | None:
    """通过非阻塞缓存子进程返回当前 git 分支名称。"""
    state = _git_branch_state
    now = time.monotonic()

    # 如果之前启动的进程已完成，收集结果
    if state.proc is not None:
        returncode = state.proc.poll()
        if returncode is not None:
            try:
                stdout, _ = state.proc.communicate()
                new_branch = stdout.strip() or None
                # 分支已变更 — 丢弃任何进行中的状态子进程，以避免写入旧分支的过时结果，
                # 然后强制立即刷新。
                if new_branch != state.branch:
                    if _git_status_state.proc is not None:
                        with contextlib.suppress(Exception):
                            _git_status_state.proc.terminate()
                        _git_status_state.proc = None
                    _git_status_state.timestamp = 0.0
                state.branch = new_branch
            except Exception:
                state.branch = None
            state.proc = None

    # 当 TTL 已过期且无进程运行时，启动新进程
    if state.timestamp + _GIT_BRANCH_TTL <= now and state.proc is None:
        state.timestamp = now
        try:
            state.proc = subprocess.Popen(
                ["git", "branch", "--show-current"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            state.branch = None

    return state.branch


def _get_git_status() -> tuple[bool, int, int]:
    """通过非阻塞缓存子进程返回 (dirty, ahead, behind)。

    运行 ``git status --porcelain -b``（包含未跟踪文件，因此新创建的文件
    会显示为 dirty）。TTL 比分支检查更长，因为文件树扫描开销较大。
    """
    state = _git_status_state
    now = time.monotonic()

    if state.proc is not None:
        returncode = state.proc.poll()
        if returncode is not None:
            try:
                stdout, _ = state.proc.communicate()
                dirty = False
                ahead = 0
                behind = 0
                for line in stdout.splitlines():
                    if line.startswith("## "):
                        m = _GIT_STATUS_AB_RE.search(line)
                        if m:
                            ahead = int(m.group(1) or 0)
                            behind = int(m.group(2) or 0)
                    elif line.strip():
                        dirty = True
                state.dirty = dirty
                state.ahead = ahead
                state.behind = behind
            except Exception:
                pass
            state.proc = None
        elif now - state.timestamp > _GIT_STATUS_TTL:
            # 子进程卡住（例如大量未跟踪文件导致 OS 管道缓冲区满）。
            # 终止它以避免工具栏永久冻结；在下一次 TTL 后重试。
            with contextlib.suppress(Exception):
                state.proc.terminate()
            state.proc = None
            state.timestamp = now  # 将下一次启动推迟一个完整的 TTL

    if state.timestamp + _GIT_STATUS_TTL <= now and state.proc is None:
        state.timestamp = now
        with contextlib.suppress(Exception):
            state.proc = subprocess.Popen(
                ["git", "status", "--porcelain", "-b"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

    return state.dirty, state.ahead, state.behind


def _format_git_badge(branch: str, dirty: bool, ahead: int, behind: int) -> str:
    """格式化分支名称及可选状态徽章：``main [± ↑3↓1]``。"""
    parts: list[str] = []
    if dirty:
        parts.append("±")
    sync = ""
    if ahead:
        sync += f"↑{ahead}"
    if behind:
        sync += f"↓{behind}"
    if sync:
        parts.append(sync)
    if not parts:
        return branch
    return f"{branch} [{' '.join(parts)}]"


def _shorten_cwd(path: str) -> str:
    """将 *path* 中的主目录前缀替换为 ``~``。"""
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


def _display_width(text: str) -> int:
    """返回 *text* 的终端列宽度，处理宽 Unicode 字符。"""
    return sum(get_cwidth(c) for c in text)


def _truncate_left(text: str, max_cols: int) -> str:
    """从左侧截断 *text*，若超过 *max_cols* 则在开头添加省略号。"""
    if max_cols <= 0:
        return ""
    if _display_width(text) <= max_cols:
        return text
    ellipsis = "…"
    budget = max_cols - _display_width(ellipsis)
    chars: list[str] = []
    width = 0
    for ch in reversed(text):
        w = get_cwidth(ch)
        if width + w > budget:
            break
        chars.append(ch)
        width += w
    return ellipsis + "".join(reversed(chars))


def _truncate_right(text: str, max_cols: int) -> str:
    """从右侧截断 *text*，若超过 *max_cols* 则在末尾添加省略号。"""
    if max_cols <= 0:
        return ""
    if _display_width(text) <= max_cols:
        return text
    ellipsis = "…"
    budget = max_cols - _display_width(ellipsis)
    chars: list[str] = []
    width = 0
    for ch in text:
        w = get_cwidth(ch)
        if width + w > budget:
            break
        chars.append(ch)
        width += w
    return "".join(chars) + ellipsis


@dataclass(slots=True)
class _ToastEntry:
    """Toast 消息条目。

    Attributes:
        topic: 主题标识，每个非 None 主题在队列中只能有一个 toast。
        message: 消息内容。
        expires_at: 过期时间戳。
    """

    topic: str | None
    message: str
    expires_at: float


class RunningPromptDelegate(Protocol):
    """运行中提示词的委托协议。

    用于处理运行中的提示词界面交互。
    """

    modal_priority: int

    def render_running_prompt_body(self, columns: int) -> AnyFormattedText: ...

    def running_prompt_placeholder(self) -> AnyFormattedText | None: ...

    def running_prompt_allows_text_input(self) -> bool: ...

    def running_prompt_hides_input_buffer(self) -> bool: ...

    def running_prompt_accepts_submission(self) -> bool: ...

    def should_handle_running_prompt_key(self, key: str) -> bool: ...

    def handle_running_prompt_key(self, key: str, event: KeyPressEvent) -> None: ...


_toast_queues: dict[Literal["left", "right"], deque[_ToastEntry]] = {
    "left": deque(),
    "right": deque(),
}
"""待显示的 toast 队列，包括当前正在显示的 toast（队列中的第一个）。"""


def toast(
    message: str,
    duration: float = 5.0,
    topic: str | None = None,
    immediate: bool = False,
    position: Literal["left", "right"] = "left",
) -> None:
    queue = _toast_queues[position]
    duration = max(duration, _IDLE_REFRESH_INTERVAL)
    entry = _ToastEntry(topic=topic, message=message, expires_at=time.monotonic() + duration)
    if topic is not None:
        # 移除具有相同主题的现有 toast
        for existing in list(queue):
            if existing.topic == topic:
                queue.remove(existing)
    if immediate:
        queue.appendleft(entry)
    else:
        queue.append(entry)


def _current_toast(position: Literal["left", "right"] = "left") -> _ToastEntry | None:
    queue = _toast_queues[position]
    now = time.monotonic()
    while queue and queue[0].expires_at <= now:
        queue.popleft()
    if not queue:
        return None
    return queue[0]


def _build_toolbar_tips(clipboard_available: bool) -> list[str]:
    tips = [
        "ctrl-x: toggle mode",
        "shift-tab: plan mode",
        "ctrl-o: editor",
        "ctrl-j: newline",
        "/feedback: send feedback",
        "/theme: switch dark/light",
    ]
    if clipboard_available:
        tips.append("ctrl-v: paste clipboard")
    tips.append("@: mention files")
    return tips


_TIP_SEPARATOR = " | "


class CustomPromptSession:
    def __init__(
        self,
        *,
        status_provider: Callable[[], StatusSnapshot],
        status_block_provider: Callable[[int], AnyFormattedText | None] | None = None,
        fast_refresh_provider: Callable[[], bool] | None = None,
        background_task_count_provider: Callable[[], int] | None = None,
        model_capabilities: set[ModelCapability],
        model_name: str | None,
        thinking: bool,
        agent_mode_slash_commands: Sequence[SlashCommand[Any]],
        shell_mode_slash_commands: Sequence[SlashCommand[Any]],
        editor_command_provider: Callable[[], str] = lambda: "",
        plan_mode_toggle_callback: Callable[[], Awaitable[bool]] | None = None,
        current_book_provider: Callable[[], str | None] | None = None,
    ) -> None:
        history_dir = get_share_dir() / "user-history"
        history_dir.mkdir(parents=True, exist_ok=True)
        work_dir_id = md5(str(KaosPath.cwd()).encode(encoding="utf-8")).hexdigest()
        self._history_file = (history_dir / work_dir_id).with_suffix(".jsonl")
        self._status_provider = status_provider
        self._status_block_provider = status_block_provider
        self._fast_refresh_provider = fast_refresh_provider
        self._background_task_count_provider = background_task_count_provider
        self._editor_command_provider = editor_command_provider
        self._plan_mode_toggle_callback = plan_mode_toggle_callback
        self._current_book_provider = current_book_provider
        self._model_capabilities = model_capabilities
        self._model_name = model_name
        self._last_history_content: str | None = None
        self._mode: PromptMode = PromptMode.AGENT
        self._thinking = thinking
        self._placeholder_manager = PromptPlaceholderManager()
        # 保留旧属性以兼容测试和外部导入。
        self._attachment_cache = self._placeholder_manager.attachment_cache
        self._last_tip_rotate_time: float = time.monotonic()
        self._last_submission_was_running = False
        self._last_input_activity_time: float = 0.0
        self._input_activity_event: asyncio.Event = asyncio.Event()
        self._running_prompt_previous_mode: PromptMode | None = None
        self._running_prompt_delegate: RunningPromptDelegate | None = None
        self._modal_delegates: list[RunningPromptDelegate] = []
        self._prompt_buffer_container: ConditionalContainer | None = None
        self._last_ui_state: PromptUIState = PromptUIState.NORMAL_INPUT
        self._suspended_buffer_document: Document | None = None
        clipboard_available = is_clipboard_available()
        self._tips = _build_toolbar_tips(clipboard_available)
        self._tip_rotation_index: int = random.randrange(len(self._tips)) if self._tips else 0

        history_entries = _load_history_entries(self._history_file)
        history = InMemoryHistory()
        for entry in history_entries:
            history.append_string(entry.content)

        if history_entries:
            # 用于连续去重
            self._last_history_content = history_entries[-1].content

        # 构建补全器
        self._agent_mode_completer = merge_completers(
            [
                SlashCommandCompleter(agent_mode_slash_commands),
                # TODO(kaos): we need an async KaosFileMentionCompleter
                LocalFileMentionCompleter(KaosPath.cwd().unsafe_to_local_path()),
            ],
            deduplicate=True,
        )
        self._shell_mode_completer = SlashCommandCompleter(shell_mode_slash_commands)

        # 构建键绑定
        _kb = KeyBindings()

        @_kb.add("enter", filter=has_completions)
        def _(event: KeyPressEvent) -> None:
            """当按下 Enter 且显示补全列表时，接受第一个补全项。"""
            buff = event.current_buffer
            if buff.complete_state and buff.complete_state.completions:
                # 获取当前补全项，若无选中则使用第一个
                completion = buff.complete_state.current_completion
                if not completion:
                    completion = buff.complete_state.completions[0]
                buff.apply_completion(completion)

        @_kb.add("c-x", eager=True)
        def _(event: KeyPressEvent) -> None:
            if self._active_prompt_delegate() is not None:
                return
            self._mode = self._mode.toggle()
            # 应用模式特定设置
            self._apply_mode(event)
            # 重绘 UI
            event.app.invalidate()

        @_kb.add("s-tab", eager=True)
        def _(event: KeyPressEvent) -> None:
            """通过 Shift+Tab 切换计划模式。"""
            if self._active_prompt_delegate() is not None:
                return
            if self._plan_mode_toggle_callback is not None:

                async def _toggle() -> None:
                    assert self._plan_mode_toggle_callback is not None
                    new_state = await self._plan_mode_toggle_callback()
                    if new_state:
                        toast("plan mode ON", topic="plan_mode", duration=3.0, immediate=True)
                    else:
                        toast("plan mode OFF", topic="plan_mode", duration=3.0, immediate=True)
                    event.app.invalidate()

                event.app.create_background_task(_toggle())
            event.app.invalidate()

        @_kb.add("escape", "enter", eager=True)
        @_kb.add("c-j", eager=True)
        def _(event: KeyPressEvent) -> None:
            """当按下 Alt-Enter 或 Ctrl-J 时插入换行符。"""
            event.current_buffer.insert_text("\n")

        @_kb.add("c-o", eager=True)
        def _(event: KeyPressEvent) -> None:
            """在外部编辑器中打开当前缓冲区内容。"""
            self._open_in_external_editor(event)

        @_kb.add(
            "up",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("up")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("up", event)

        @_kb.add(
            "down",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("down")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("down", event)

        @_kb.add(
            "left",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("left")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("left", event)

        @_kb.add(
            "right",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("right")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("right", event)

        @_kb.add(
            "tab",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("tab")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("tab", event)

        @_kb.add(
            "enter",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("enter")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("enter", event)

        @_kb.add(
            "space",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("space")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("space", event)

        @_kb.add(
            "c-e",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("c-e")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("c-e", event)

        @_kb.add(
            "c-c",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("c-c")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("c-c", event)

        @_kb.add(
            "c-d",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("c-d")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("c-d", event)

        @_kb.add(
            "escape",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("escape")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("escape", event)

        @_kb.add(
            "1",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("1")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("1", event)

        @_kb.add(
            "2",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("2")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("2", event)

        @_kb.add(
            "3",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("3")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("3", event)

        @_kb.add(
            "4",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("4")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("4", event)

        @_kb.add(
            "5",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("5")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("5", event)

        @_kb.add(
            "6",
            eager=True,
            filter=Condition(lambda: self._should_handle_running_prompt_key("6")),
        )
        def _(event: KeyPressEvent) -> None:
            self._handle_running_prompt_key("6", event)

        @_kb.add(Keys.BracketedPaste, eager=True)
        def _(event: KeyPressEvent) -> None:
            self._handle_bracketed_paste(event)

        if clipboard_available:

            @_kb.add("c-v", eager=True)
            def _(event: KeyPressEvent) -> None:
                if self._try_paste_media(event):
                    return
                clipboard_data = event.app.clipboard.get_data()
                if clipboard_data is None:  # type: ignore[reportUnnecessaryComparison]
                    return
                self._insert_pasted_text(event.current_buffer, clipboard_data.text)
                event.app.invalidate()

            clipboard = PyperclipClipboard()
        else:
            clipboard = None

        self._session = PromptSession[str](
            message=self._render_message,
            # prompt_continuation=FormattedText([("fg:#4d4d4d", "... ")]),
            completer=self._agent_mode_completer,
            complete_while_typing=True,
            reserve_space_for_menu=6,
            key_bindings=_kb,
            clipboard=clipboard,
            history=history,
            bottom_toolbar=self._render_bottom_toolbar,
            style=get_prompt_style(),
        )
        self._session.default_buffer.read_only = Condition(
            lambda: (
                (delegate := self._active_prompt_delegate()) is not None
                and not delegate.running_prompt_allows_text_input()
            )
        )
        self._install_slash_completion_menu()
        self._install_prompt_buffer_visibility()
        self._apply_mode()

        # 当文本变更时触发补全，例如使用退格键删除文本时。
        @self._session.default_buffer.on_text_changed.add_handler
        def _(buffer: Buffer) -> None:
            self._last_input_activity_time = time.monotonic()
            self._input_activity_event.set()
            if buffer.complete_while_typing():
                buffer.start_completion()

        self._status_refresh_task: asyncio.Task[None] | None = None

    def _install_slash_completion_menu(self) -> None:
        float_container = _find_prompt_float_container(self._session.layout.container)
        if not isinstance(float_container, FloatContainer):
            return

        slash_menu_filter = (
            has_focus(self._session.default_buffer)
            & has_completions
            & ~is_done
            & Condition(self._should_show_slash_completion_menu)
        )
        slash_menu = ConditionalContainer(
            Window(
                content=SlashCommandMenuControl(left_padding=self._slash_menu_left_padding),
                dont_extend_height=True,
                height=Dimension(max=10),
                style="class:slash-completion-menu",
            ),
            filter=slash_menu_filter,
        )
        float_container.floats.insert(
            0,
            Float(
                left=0,
                right=0,
                ycursor=True,
                content=slash_menu,
                z_index=10**8,
            ),
        )

        original_float = next(
            (
                float_
                for float_ in float_container.floats[1:]
                if isinstance(float_.content, CompletionsMenu)
            ),
            None,
        )
        if original_float is None:
            return
        original_float.content = ConditionalContainer(
            original_float.content,
            filter=~Condition(self._should_show_slash_completion_menu),
        )

    def _install_prompt_buffer_visibility(self) -> None:
        buffer_container = _find_default_buffer_container(
            self._session.layout.container,
            self._session.default_buffer,
        )
        if buffer_container is None:
            return
        buffer_container.filter = buffer_container.filter & Condition(
            self._should_render_input_buffer
        )
        self._prompt_buffer_container = buffer_container

    def _should_show_slash_completion_menu(self) -> bool:
        document = self._session.default_buffer.document
        return SlashCommandCompleter.should_complete(document)

    def _slash_menu_left_padding(self) -> int:
        if self._mode == PromptMode.SHELL:
            return max(1, get_cwidth(f"{PROMPT_SYMBOL_SHELL} ") - 2)
        if self._status_provider().plan_mode:
            return max(1, get_cwidth(f"{PROMPT_SYMBOL_PLAN} ") - 2)
        symbol = PROMPT_SYMBOL_THINKING if self._thinking else PROMPT_SYMBOL
        return max(1, get_cwidth(f"{symbol} ") - 2)

    def _render_message(self) -> FormattedText:
        if self._mode == PromptMode.SHELL:
            return self._render_shell_prompt_message()
        return self._render_agent_prompt_message()

    def _render_shell_prompt_message(self) -> FormattedText:
        app = get_app_or_none()
        columns = app.output.get_size().columns if app is not None else 80
        fragments: FormattedText = FormattedText()
        body = self._render_agent_prompt_body(columns)
        if body:
            fragments.extend(body)
            if not body[-1][1].endswith("\n"):
                fragments.append(("", "\n"))
        if self._active_modal_delegate() is not None:
            return fragments
        if body:
            fragments.append(("", "\n"))
            fragments.append(("class:running-prompt-separator", "─" * max(0, columns)))
            fragments.append(("", "\n"))
        fragments.append(("bold", f"{PROMPT_SYMBOL_SHELL} "))
        return fragments

    def _open_in_external_editor(self, event: KeyPressEvent) -> None:
        """在外部编辑器中打开当前缓冲区内容。"""
        from prompt_toolkit.application.run_in_terminal import run_in_terminal

        from novel_cli.utils.editor import edit_text_in_editor, get_editor_command

        configured = self._editor_command_provider()

        if get_editor_command(configured) is None:
            toast("No editor found. Set $VISUAL/$EDITOR or run /editor.")
            return

        buff = event.current_buffer
        original_text = buff.text
        editor_text = self._get_placeholder_manager().expand_for_editor(original_text)

        async def _run_editor() -> None:
            result = await run_in_terminal(
                lambda: edit_text_in_editor(editor_text, configured), in_executor=True
            )
            if result is not None:
                refolded = self._get_placeholder_manager().refold_after_editor(
                    result, original_text
                )
                buff.document = Document(text=refolded, cursor_position=len(refolded))

        event.app.create_background_task(_run_editor())

    def _apply_mode(self, event: KeyPressEvent | None = None) -> None:
        # 将模式应用到活动缓冲区（而非 PromptSession 本身）
        try:
            buff = event.current_buffer if event is not None else self._session.default_buffer
        except Exception:
            buff = None

        if self._mode == PromptMode.SHELL:
            if buff is not None:
                buff.completer = self._shell_mode_completer
        else:
            if buff is not None:
                buff.completer = self._agent_mode_completer
        self._sync_erase_when_done()

    def _sync_erase_when_done(self) -> None:
        app = getattr(self._session, "app", None)
        if app is not None:
            app.erase_when_done = self._mode == PromptMode.AGENT

    def _active_modal_delegate(self) -> RunningPromptDelegate | None:
        modal_delegates = getattr(self, "_modal_delegates", [])
        if not modal_delegates:
            return None
        _, delegate = max(
            enumerate(modal_delegates),
            key=lambda item: (item[1].modal_priority, item[0]),
        )
        return delegate

    def _active_prompt_delegate(self) -> RunningPromptDelegate | None:
        if delegate := self._active_modal_delegate():
            return delegate
        return getattr(self, "_running_prompt_delegate", None)

    def _active_ui_state(self) -> PromptUIState:
        delegate = self._active_modal_delegate()
        if delegate is None:
            return PromptUIState.NORMAL_INPUT
        if delegate.running_prompt_hides_input_buffer():
            return PromptUIState.MODAL_HIDDEN_INPUT
        if delegate.running_prompt_allows_text_input():
            return PromptUIState.MODAL_TEXT_INPUT
        return PromptUIState.NORMAL_INPUT

    def _should_render_input_buffer(self) -> bool:
        return self._active_ui_state() != PromptUIState.MODAL_HIDDEN_INPUT

    def _should_handle_running_prompt_key(self, key: str) -> bool:
        delegate = self._active_prompt_delegate()
        return delegate is not None and delegate.should_handle_running_prompt_key(key)

    def _handle_running_prompt_key(self, key: str, event: KeyPressEvent) -> None:
        delegate = self._active_prompt_delegate()
        if delegate is None:
            return
        delegate.handle_running_prompt_key(key, event)
        event.app.invalidate()

    def invalidate(self) -> None:
        self._sync_prompt_ui_state()
        app = get_app_or_none()
        if app is not None:
            app.invalidate()

    def _sync_prompt_ui_state(self) -> None:
        new_state = self._active_ui_state()
        old_state = getattr(self, "_last_ui_state", PromptUIState.NORMAL_INPUT)
        buffer = self._session.default_buffer

        if (
            old_state != PromptUIState.MODAL_HIDDEN_INPUT
            and new_state == PromptUIState.MODAL_HIDDEN_INPUT
        ):
            if self._suspended_buffer_document is None and buffer.text:
                self._suspended_buffer_document = buffer.document
                buffer.set_document(Document(), bypass_readonly=True)
        elif (
            old_state == PromptUIState.MODAL_HIDDEN_INPUT
            and new_state != PromptUIState.MODAL_HIDDEN_INPUT
        ):
            if self._suspended_buffer_document is not None and not buffer.text:
                buffer.set_document(self._suspended_buffer_document, bypass_readonly=True)
            self._suspended_buffer_document = None

        self._last_ui_state = new_state

    def _render_agent_prompt_message(self) -> FormattedText:
        app = get_app_or_none()
        columns = app.output.get_size().columns if app is not None else 80
        fragments: FormattedText = FormattedText()
        body = self._render_agent_prompt_body(columns)
        if body:
            fragments.extend(body)
            if not body[-1][1].endswith("\n"):
                fragments.append(("", "\n"))
        if self._active_modal_delegate() is not None:
            return fragments
        fragments.append(("", "\n"))
        fragments.append(("class:running-prompt-separator", "─" * max(0, columns)))
        fragments.append(("", "\n"))
        fragments.extend(self._render_agent_prompt_label())
        return fragments

    def _render_agent_prompt_body(self, columns: int) -> FormattedText:
        delegate = self._active_prompt_delegate()
        if delegate is None:
            return self._render_status_block(columns)
        return to_formatted_text(delegate.render_running_prompt_body(columns))

    def _render_status_block(self, columns: int) -> FormattedText:
        status_block_provider = getattr(self, "_status_block_provider", None)
        if status_block_provider is None:
            return FormattedText([])
        block = status_block_provider(columns)
        if block is None:
            return FormattedText([])
        return to_formatted_text(block)

    def _render_agent_prompt_label(self) -> FormattedText:
        status = self._status_provider()
        if status.plan_mode:
            return FormattedText([(get_toolbar_colors().plan_prompt, f"{PROMPT_SYMBOL_PLAN} ")])
        symbol = PROMPT_SYMBOL_THINKING if self._thinking else PROMPT_SYMBOL
        return FormattedText([("", f"{symbol} ")])

    def __enter__(self) -> CustomPromptSession:
        if self._status_refresh_task is not None and not self._status_refresh_task.done():
            return self

        async def _refresh() -> None:
            try:
                while True:
                    app = get_app_or_none()
                    if app is not None:
                        app.invalidate()

                    try:
                        asyncio.get_running_loop()
                    except RuntimeError:
                        logger.warning("No running loop found, exiting status refresh task")
                        self._status_refresh_task = None
                        break

                    interval = (
                        _RUNNING_REFRESH_INTERVAL
                        if self._active_prompt_delegate() is not None
                        or (
                            self._fast_refresh_provider is not None
                            and self._fast_refresh_provider()
                        )
                        else _IDLE_REFRESH_INTERVAL
                    )
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                # 优雅退出
                pass

        self._status_refresh_task = asyncio.create_task(_refresh())
        return self

    def __exit__(self, *_) -> None:
        if self._status_refresh_task is not None and not self._status_refresh_task.done():
            self._status_refresh_task.cancel()
        self._status_refresh_task = None

    def _get_placeholder_manager(self) -> PromptPlaceholderManager:
        manager = getattr(self, "_placeholder_manager", None)
        if manager is None:
            attachment_cache = getattr(self, "_attachment_cache", None)
            manager = PromptPlaceholderManager(attachment_cache=attachment_cache)
            self._placeholder_manager = manager
            self._attachment_cache = manager.attachment_cache
        return manager

    def _insert_pasted_text(self, buffer: Buffer, text: str) -> None:
        normalized = normalize_pasted_text(text)
        if self._mode != PromptMode.AGENT:
            buffer.insert_text(normalized)
            return
        token_or_text = self._get_placeholder_manager().maybe_placeholderize_pasted_text(normalized)
        buffer.insert_text(token_or_text)

    def _handle_bracketed_paste(self, event: KeyPressEvent) -> None:
        self._insert_pasted_text(event.current_buffer, event.data)
        event.app.invalidate()

    def _try_paste_media(self, event: KeyPressEvent) -> bool:
        """尝试从剪贴板粘贴媒体内容。

        读取剪贴板一次并处理所有检测到的内容：
        非图像文件（视频、PDF 等）作为路径插入，
        图像文件被缓存并作为占位符插入。
        如果有任何媒体内容被插入则返回 True。
        """
        result = grab_media_from_clipboard()
        if result is None:
            return False

        parts: list[str] = []

        # 1. 插入文件路径（视频、PDF 等）
        if result.file_paths:
            logger.debug("Pasted {count} file path(s) from clipboard", count=len(result.file_paths))
            for p in result.file_paths:
                text = str(p)
                if self._mode == PromptMode.SHELL:
                    text = shlex.quote(text)
                parts.append(text)

        # 2. 通过缓存插入图像。
        if result.images:
            if "image_in" not in self._model_capabilities:
                console.print(
                    "[yellow]Image input is not supported by the selected LLM model[/yellow]"
                )
            else:
                for image in result.images:
                    token = self._get_placeholder_manager().create_image_placeholder(image)
                    if token is None:
                        continue
                    logger.debug(
                        "Pasted image from clipboard placeholder: {token}, {image_size}",
                        token=token,
                        image_size=image.size,
                    )
                    parts.append(token)

        if parts:
            event.current_buffer.insert_text(" ".join(parts))
        event.app.invalidate()
        return bool(parts)

    def set_prefill_text(self, text: str) -> None:
        """使用给定文本预填充输入缓冲区。

        必须在提示词会话创建后、第一次 prompt_async 调用前执行。
        该文本将在下一次提示词中作为可编辑的默认输入显示。
        """
        self._prefill_text = text

    async def prompt_next(self) -> UserInput:
        return await self._prompt_once(append_history=None)

    @property
    def last_submission_was_running(self) -> bool:
        return getattr(self, "_last_submission_was_running", False)

    def has_pending_input(self) -> bool:
        return bool(self._session.default_buffer.text)

    def had_recent_input_activity(self, *, within_s: float) -> bool:
        if self._last_input_activity_time <= 0:
            return False
        return (time.monotonic() - self._last_input_activity_time) <= within_s

    def recent_input_activity_remaining(self, *, within_s: float) -> float:
        if self._last_input_activity_time <= 0:
            return 0.0
        elapsed = time.monotonic() - self._last_input_activity_time
        return max(0.0, within_s - elapsed)

    async def wait_for_input_activity(self) -> None:
        await self._input_activity_event.wait()
        self._input_activity_event.clear()

    def attach_running_prompt(self, delegate: RunningPromptDelegate) -> None:
        current = getattr(self, "_running_prompt_delegate", None)
        if current is delegate:
            return
        if current is None:
            self._running_prompt_previous_mode = self._mode
        self._running_prompt_delegate = delegate
        self._mode = PromptMode.AGENT
        self._apply_mode()
        self.invalidate()

    def detach_running_prompt(self, delegate: RunningPromptDelegate) -> None:
        if getattr(self, "_running_prompt_delegate", None) is not delegate:
            return
        previous_mode = getattr(self, "_running_prompt_previous_mode", None)
        self._running_prompt_delegate = None
        self._running_prompt_previous_mode = None
        if previous_mode is not None:
            self._mode = previous_mode
        self._apply_mode()
        self.invalidate()

    def attach_modal(self, delegate: RunningPromptDelegate) -> None:
        modal_delegates: list[RunningPromptDelegate] | None = getattr(
            self, "_modal_delegates", None
        )
        if modal_delegates is None:
            modal_delegates = []
            self._modal_delegates = modal_delegates
        if delegate in modal_delegates:
            return
        modal_delegates.append(delegate)
        self.invalidate()

    def detach_modal(self, delegate: RunningPromptDelegate) -> None:
        modal_delegates = getattr(self, "_modal_delegates", None)
        if not modal_delegates or delegate not in modal_delegates:
            return
        modal_delegates.remove(delegate)
        self.invalidate()

    def running_prompt_accepts_submission(self) -> bool:
        delegate = self._active_prompt_delegate()
        if delegate is None:
            return False
        return delegate.running_prompt_accepts_submission()

    async def _prompt_once(self, *, append_history: bool | None) -> UserInput:
        placeholder = None
        if (delegate := self._active_prompt_delegate()) is not None:
            placeholder = delegate.running_prompt_placeholder()
        # 消费一次性预填充文本（若已设置）
        default = getattr(self, "_prefill_text", None) or ""
        self._prefill_text = None
        with patch_stdout(raw=True):
            command = str(
                await self._session.prompt_async(placeholder=placeholder, default=default)
            ).strip()
            command = command.replace("\x00", "")  # 防止意外插入空字节
            # 清理可能来自 Windows 剪贴板的 UTF-16 代理对
            command = sanitize_surrogates(command)
        was_running = self.running_prompt_accepts_submission()
        self._last_submission_was_running = was_running
        if append_history is None:
            append_history = not was_running
        if append_history:
            self._append_history_entry(command)
        self._tip_rotation_index += 1
        return self._build_user_input(command)

    def _build_user_input(self, command: str) -> UserInput:
        resolved = self._get_placeholder_manager().resolve_command(command)

        return UserInput(
            mode=self._mode,
            command=resolved.display_command,
            resolved_command=resolved.resolved_text,
            content=resolved.content,
        )

    def _append_history_entry(self, text: str) -> None:
        safe_history_text = self._get_placeholder_manager().serialize_for_history(text).strip()
        entry = _HistoryEntry(content=safe_history_text)
        if not entry.content:
            return

        # 若与上一条相同则跳过
        if entry.content == self._last_history_content:
            return

        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with self._history_file.open("a", encoding="utf-8") as f:
                f.write(entry.model_dump_json(ensure_ascii=False) + "\n")
            self._last_history_content = entry.content
        except OSError as exc:
            logger.warning(
                "Failed to append user history entry: {file} ({error})",
                file=self._history_file,
                error=exc,
            )

    def _render_bottom_toolbar(self) -> FormattedText:
        if (
            hasattr(self, "_session")
            and self._should_show_slash_completion_menu()
            and self._session.default_buffer.complete_state is not None
        ):
            return FormattedText([])
        app = get_app_or_none()
        assert app is not None
        columns = app.output.get_size().columns

        fragments: list[tuple[str, str]] = []
        tc = get_toolbar_colors()

        fragments.append((tc.separator, "─" * columns))
        fragments.append(("", "\n"))

        remaining = columns

        # 基于时间的提示轮换（每 30 秒，独立于用户提交）
        now = time.monotonic()
        if now - self._last_tip_rotate_time >= _TIP_ROTATE_INTERVAL:
            self._tip_rotation_index += 1
            self._last_tip_rotate_time = now

        # 状态标志：yolo / plan
        status = self._status_provider()
        if status.yolo_enabled:
            fragments.extend([(tc.yolo_label, "yolo"), ("", "  ")])
            remaining -= 6  # "yolo" = 4, "  " = 2
        if status.plan_mode:
            fragments.extend([(tc.plan_label, "plan"), ("", "  ")])
            remaining -= 6

        # 模式指示器（agent / shell）+ 模型名称 + 思考指示器。
        # 在窄终端上优雅降级：
        #   完整："agent (model-name ○)" → 中等："agent ○" → 最简："agent"
        mode = str(self._mode)
        if self._mode == PromptMode.AGENT and self._model_name:
            thinking_dot = "●" if self._thinking else "○"
            mode_full = f"{mode} ({self._model_name} {thinking_dot})"
            mode_mid = f"{mode} {thinking_dot}"
            if _display_width(mode_full) <= remaining - 2:
                mode = mode_full
            elif _display_width(mode_mid) <= remaining - 2:
                mode = mode_mid
            # 否则：保留最简模式名称 — 模型名称和圆点均被丢弃
        fragments.extend([("", mode), ("", "  ")])
        remaining -= _display_width(mode) + 2

        # 当前选书
        book_provider = getattr(self, "_current_book_provider", None)
        if book_provider:
            book_name = book_provider()
            if book_name:
                book_text = f"\U0001f4d6 {book_name}"
                book_width = _display_width(book_text)
                if remaining >= book_width + 2:
                    fragments.extend([(tc.cwd, book_text), ("", "  ")])
                    remaining -= book_width + 2

        # 当前工作目录（从左侧截断）+ git 分支及状态徽章
        # 在窄终端上优雅降级：完整 → 仅 cwd → 截断 cwd → 跳过
        cwd = _truncate_left(_shorten_cwd(str(KaosPath.cwd())), _MAX_CWD_COLS)
        branch = _get_git_branch()
        if branch:
            dirty, ahead, behind = _get_git_status()
            branch = _truncate_right(branch, _MAX_BRANCH_COLS)
            badge = _format_git_badge(branch, dirty, ahead, behind)
            cwd_text = f"{cwd}  {badge}"
        else:
            cwd_text = cwd
        cwd_w = _display_width(cwd_text)
        if cwd_w > remaining - 2:
            cwd_text = cwd  # 丢弃徽章
            cwd_w = _display_width(cwd_text)
        if cwd_w > remaining - 2:
            cwd_text = _truncate_right(cwd, max(0, remaining - 2))
            cwd_w = _display_width(cwd_text)
        if cwd_text and remaining >= cwd_w + 2:
            fragments.extend([(tc.cwd, cwd_text), ("", "  ")])
            remaining -= cwd_w + 2

        # 活动的后台 bash 任务数量
        bg_count = (
            self._background_task_count_provider() if self._background_task_count_provider else 0
        )
        if bg_count > 0:
            bg_text = f"⚙ bash: {bg_count}"
            bg_width = _display_width(bg_text)
            if remaining >= bg_width + 2:
                fragments.extend([(tc.bg_tasks, bg_text), ("", "  ")])
                remaining -= bg_width + 2

        # 提示填充第 1 行剩余空间
        tip_text = self._get_two_rotating_tips()
        if tip_text and _display_width(tip_text) > remaining:
            tip_text = self._get_one_rotating_tip()
        if tip_text and _display_width(tip_text) <= remaining:
            fragments.append((tc.tip, tip_text))

        # ── 第 2 行：toast（左侧）+ context（右侧）— 始终渲染 ──────
        fragments.append(("", "\n"))

        right_text = self._render_right_span(status)
        right_width = _display_width(right_text)

        left_toast = _current_toast("left")
        if left_toast is not None:
            max_left = max(0, columns - right_width - 2)
            if max_left > 0:
                left_text = left_toast.message
                if _display_width(left_text) > max_left:
                    left_text = _truncate_right(left_text, max_left)
                left_width = _display_width(left_text)
                fragments.append(("", left_text))
            else:
                left_width = 0
        else:
            left_width = 0

        fragments.append(("", " " * max(0, columns - left_width - right_width)))
        fragments.append(("", right_text))

        return FormattedText(fragments)

    def _get_two_rotating_tips(self) -> str | None:
        """返回恰好 2 个提示的字符串，若不足则返回更少。"""
        n = len(self._tips)
        if n == 0:
            return None
        if n == 1:
            return self._tips[0]
        offset = self._tip_rotation_index % n
        tip1 = self._tips[offset]
        tip2 = self._tips[(offset + 1) % n]
        return f"{tip1}{_TIP_SEPARATOR}{tip2}"

    def _get_one_rotating_tip(self) -> str | None:
        """返回当前轮换中的单个提示。"""
        if not self._tips:
            return None
        return self._tips[self._tip_rotation_index % len(self._tips)]

    @staticmethod
    def _render_right_span(status: StatusSnapshot) -> str:
        current_toast = _current_toast("right")
        if current_toast is None:
            return format_context_status(
                status.context_usage,
                status.context_tokens,
                status.max_context_tokens,
            )
        return current_toast.message
