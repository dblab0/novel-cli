"""工具辅助函数模块。

提供工具描述加载、行截断、工具结果构建器等辅助功能。
"""

import re
from pathlib import Path

from jinja2 import Environment, Undefined
from kosong.tooling import BriefDisplayBlock, DisplayBlock, ToolError, ToolReturnValue
from kosong.utils.typing import JsonType


class _KeepPlaceholderUndefined(Undefined):
    """保留未定义占位符的 Jinja2 Undefined 子类。

    当模板变量未定义时，保留原始占位符格式（如 ${var}）而不是抛出异常。

    Attributes:
        _undefined_name: 未定义变量的名称。
    """

    def __str__(self) -> str:
        """返回未定义变量的占位符字符串表示。

        Returns:
            格式为 "${变量名}" 的字符串，或空字符串（如果变量名为 None）。
        """
        if self._undefined_name is None:
            return ""
        return f"${{{self._undefined_name}}}"

    __repr__ = __str__


def load_desc(path: Path, context: dict[str, object] | None = None) -> str:
    """从文件加载工具描述并通过 Jinja2 渲染。

    Args:
        path: 描述文件的路径。
        context: 模板渲染的上下文变量，可选。

    Returns:
        渲染后的描述字符串。
    """
    description = path.read_text(encoding="utf-8")
    env = Environment(
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=True,
        variable_start_string="${",
        variable_end_string="}",
        undefined=_KeepPlaceholderUndefined,
    )
    template = env.from_string(description)
    return template.render(context or {})


def truncate_line(line: str, max_length: int, marker: str = "...") -> str:
    """截断超长的行。

    如果行超过最大长度则截断，保留行首和行尾换行符。
    如果最大长度太短无法容纳标记，输出可能超过 max_length。

    Args:
        line: 要截断的行字符串。
        max_length: 最大长度限制。
        marker: 截断时使用的标记字符串，默认为 "..."。

    Returns:
        截断后的字符串，可能包含截断标记。
    """
    if len(line) <= max_length:
        return line

    # 查找行尾的换行符
    m = re.search(r"[\r\n]+$", line)
    linebreak = m.group(0) if m else ""
    end = marker + linebreak
    max_length = max(max_length, len(end))
    return line[: max_length - len(end)] + end


# 默认输出限制
DEFAULT_MAX_CHARS = 50_000
DEFAULT_MAX_LINE_LENGTH = 2000


class ToolResultBuilder:
    """工具结果构建器。

    用于构建具有字符数和行数限制的工具输出结果。

    Attributes:
        max_chars: 最大字符数限制。
        max_line_length: 单行最大长度限制，None 表示不限制。
    """

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_line_length: int | None = DEFAULT_MAX_LINE_LENGTH,
    ):
        """初始化工具结果构建器。

        Args:
            max_chars: 最大字符数限制，默认为 50000。
            max_line_length: 单行最大长度限制，默认为 2000。
        """
        self.max_chars = max_chars
        self.max_line_length = max_line_length
        self._marker = "[...truncated]"
        if max_line_length is not None:
            assert max_line_length > len(self._marker)
        self._buffer: list[str] = []
        self._n_chars = 0
        self._n_lines = 0
        self._truncation_happened = False
        self._display: list[DisplayBlock] = []
        self._extras: dict[str, JsonType] | None = None

    @property
    def is_full(self) -> bool:
        """检查输出缓冲区是否已满（达到字符限制）。

        Returns:
            如果缓冲区已满返回 True，否则返回 False。
        """
        return self._n_chars >= self.max_chars

    @property
    def n_chars(self) -> int:
        """获取当前字符计数。

        Returns:
            当前已写入的字符数。
        """
        return self._n_chars

    @property
    def n_lines(self) -> int:
        """获取当前行数。

        Returns:
            当前已写入的行数。
        """
        return self._n_lines

    def write(self, text: str) -> int:
        """将文本写入输出缓冲区。

        Args:
            text: 要写入的文本内容。

        Returns:
            实际写入的字符数。
        """
        if self.is_full:
            return 0

        lines = text.splitlines(keepends=True)
        if not lines:
            return 0

        chars_written = 0

        for line in lines:
            if self.is_full:
                break

            original_line = line
            remaining_chars = self.max_chars - self._n_chars
            limit = (
                min(remaining_chars, self.max_line_length)
                if self.max_line_length is not None
                else remaining_chars
            )
            line = truncate_line(line, limit, self._marker)
            if line != original_line:
                self._truncation_happened = True

            self._buffer.append(line)
            chars_written += len(line)
            self._n_chars += len(line)
            if line.endswith("\n"):
                self._n_lines += 1

        return chars_written

    def display(self, *blocks: DisplayBlock) -> None:
        """添加显示块到工具结果中。

        Args:
            *blocks: 要添加的显示块。
        """
        self._display.extend(blocks)

    def extras(self, **extras: JsonType) -> None:
        """添加额外数据到工具结果中。

        Args:
            **extras: 要添加的额外数据键值对。
        """
        if self._extras is None:
            self._extras = {}
        self._extras.update(extras)

    def ok(self, message: str = "", *, brief: str = "") -> ToolReturnValue:
        """创建一个成功的工具返回值。

        Args:
            message: 附加的消息内容。
            brief: 简短的描述文本。

        Returns:
            is_error=False 的 ToolReturnValue。
        """
        output = "".join(self._buffer)

        final_message = message
        if final_message and not final_message.endswith("."):
            final_message += "."
        truncation_msg = "Output is truncated to fit in the message."
        if self._truncation_happened:
            if final_message:
                final_message += f" {truncation_msg}"
            else:
                final_message = truncation_msg
        return ToolReturnValue(
            is_error=False,
            output=output,
            message=final_message,
            display=([BriefDisplayBlock(text=brief)] if brief else []) + self._display,
            extras=self._extras,
        )

    def error(self, message: str, *, brief: str) -> ToolReturnValue:
        """创建一个错误的工具返回值。

        Args:
            message: 错误消息内容。
            brief: 简短的错误描述。

        Returns:
            is_error=True 的 ToolReturnValue。
        """
        output = "".join(self._buffer)

        final_message = message
        if self._truncation_happened:
            truncation_msg = "Output is truncated to fit in the message."
            if final_message:
                final_message += f" {truncation_msg}"
            else:
                final_message = truncation_msg

        return ToolReturnValue(
            is_error=True,
            output=output,
            message=final_message,
            display=([BriefDisplayBlock(text=brief)] if brief else []) + self._display,
            extras=self._extras,
        )


class ToolRejectedError(ToolError):
    """工具被拒绝异常。

    当用户拒绝工具调用时抛出此异常。

    Attributes:
        has_feedback: 是否包含用户反馈。
    """

    has_feedback: bool = False

    def __init__(
        self,
        message: str | None = None,
        brief: str = "Rejected by user",
        has_feedback: bool = False,
    ):
        """初始化工具被拒绝异常。

        Args:
            message: 错误消息，如未提供则使用默认消息。
            brief: 简短的描述文本。
            has_feedback: 是否包含用户反馈。
        """
        super().__init__(
            message=message
            or (
                "The tool call is rejected by the user. "
                "Stop what you are doing and wait for the user to tell you how to proceed."
            ),
            brief=brief,
        )
        self.has_feedback = has_feedback
