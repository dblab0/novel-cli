"""智能体可视化渲染模块。

本模块实现智能体行为的实时可视化功能，通过消费 Wire 协议消息
并将智能体的思考、工具调用、审批请求等内容渲染到终端界面。

主要组件：
    - visualize: 主可视化循环入口函数
    - _LiveView: 基础实时视图类
    - _PromptLiveView: 带提示会话的实时视图类
    - _ContentBlock: 流式内容块，支持增量 Markdown 提交
    - _ToolCallBlock: 工具调用块，展示工具执行状态和结果
    - _NotificationBlock: 通知消息块
    - _StatusBlock: 状态信息块

功能特性：
    - 实时流式 Markdown 渲染
    - 工具调用进度展示
    - 审批/问答面板交互
    - 子智能体事件追踪
    - 键盘事件响应
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any, NamedTuple, cast

if TYPE_CHECKING:
    from markdown_it import MarkdownIt

import streamingjson  # type: ignore[reportMissingTypeStubs]
from kosong.message import Message
from kosong.tooling import ToolError, ToolOk
from prompt_toolkit.application.run_in_terminal import run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyPressEvent
from rich.console import Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.style import Style
from rich.text import Text

from novel_cli.soul import format_context_status, format_token_count
from novel_cli.tools import extract_key_argument
from novel_cli.ui.shell.approval_panel import (
    ApprovalPromptDelegate as ApprovalPromptDelegate,  # noqa: F401 — re-exported
)
from novel_cli.ui.shell.approval_panel import (
    ApprovalRequestPanel,
    show_approval_in_pager,
)
from novel_cli.ui.shell.console import console, render_to_ansi
from novel_cli.ui.shell.echo import render_user_echo, render_user_echo_text
from novel_cli.ui.shell.keyboard import KeyboardListener, KeyEvent
from novel_cli.ui.shell.prompt import (
    CustomPromptSession,
    UserInput,
)
from novel_cli.ui.shell.question_panel import (
    QuestionPromptDelegate as QuestionPromptDelegate,  # noqa: F401 — re-exported
)
from novel_cli.ui.shell.question_panel import (
    QuestionRequestPanel,
    prompt_other_input,
    show_question_body_in_pager,
)
from novel_cli.utils.aioqueue import Queue, QueueShutDown
from novel_cli.utils.logging import logger
from novel_cli.utils.rich.columns import BulletColumns
from novel_cli.utils.rich.diff_render import (
    collect_diff_hunks,
    render_diff_panel,
    render_diff_summary_panel,
)
from novel_cli.utils.rich.markdown import Markdown
from novel_cli.wire import WireUISide
from novel_cli.wire.types import (
    ApprovalRequest,
    ApprovalResponse,
    BackgroundTaskDisplayBlock,
    BriefDisplayBlock,
    CompactionBegin,
    CompactionEnd,
    ContentPart,
    DiffDisplayBlock,
    MCPLoadingBegin,
    MCPLoadingEnd,
    Notification,
    PlanDisplay,
    QuestionRequest,
    StatusUpdate,
    SteerInput,
    StepBegin,
    StepInterrupted,
    SubagentEvent,
    TextPart,
    ThinkPart,
    TodoDisplayBlock,
    ToolCall,
    ToolCallPart,
    ToolCallRequest,
    ToolResult,
    ToolReturnValue,
    TurnBegin,
    TurnEnd,
    WireMessage,
)

MAX_SUBAGENT_TOOL_CALLS_TO_SHOW = 4
MAX_LIVE_NOTIFICATIONS = 4
EXTERNAL_MESSAGE_GRACE_S = 0.1


async def visualize(
    wire: WireUISide,
    *,
    initial_status: StatusUpdate,
    cancel_event: asyncio.Event | None = None,
    prompt_session: CustomPromptSession | None = None,
    steer: Callable[[str | list[ContentPart]], None] | None = None,
    bind_running_input: Callable[[Callable[[UserInput], None], Callable[[], None]], None]
    | None = None,
    unbind_running_input: Callable[[], None] | None = None,
    on_view_ready: Callable[[Any], None] | None = None,
    on_view_closed: Callable[[], None] | None = None,
):
    """智能体行为可视化主循环。

    消费智能体事件并将智能体行为渲染到终端界面。

    Args:
        wire: 与智能体的通信通道。
        initial_status: 初始状态快照。
        cancel_event: 可被设置（如通过 ESC 键）以取消运行的事件。
        prompt_session: 自定义提示会话，用于交互式输入。
        steer: 用户输入注入回调函数。
        bind_running_input: 绑定运行时输入处理器的回调。
        unbind_running_input: 解绑运行时输入处理器的回调。
        on_view_ready: 视图就绪时的回调函数。
        on_view_closed: 视图关闭时的回调函数。
    """
    if prompt_session is not None and steer is not None:
        view = _PromptLiveView(
            initial_status,
            prompt_session=prompt_session,
            steer=steer,
            cancel_event=cancel_event,
        )
        prompt_session.attach_running_prompt(view)

        def _cancel_running_input() -> None:
            if cancel_event is not None:
                cancel_event.set()

        if bind_running_input is not None:
            bind_running_input(view.handle_local_input, _cancel_running_input)
    else:
        view = _LiveView(initial_status, cancel_event)
    if on_view_ready is not None:
        on_view_ready(view)
    try:
        await view.visualize_loop(wire)
    finally:
        if prompt_session is not None and steer is not None:
            if unbind_running_input is not None:
                unbind_running_input()
            assert isinstance(view, _PromptLiveView)
            prompt_session.detach_running_prompt(view)
        if on_view_closed is not None:
            on_view_closed()


# 思考预览最大行数
_THINKING_PREVIEW_LINES = 6
# 待处理预览最大行数
_PENDING_PREVIEW_LINES = 8
# 自闭合块类型集合（无需闭合标签的 Markdown 块）
_SELF_CLOSING_BLOCKS = frozenset(("fence", "code_block", "hr", "html_block"))
# 省略号符号
_ELLIPSIS = "..."


def _truncate_to_display_width(line: str, max_width: int) -> str:
    """截断文本以适应终端显示宽度。

    使用 rich.cells.cell_len 进行 CJK 兼容的列宽测量，
    确保文本在终端中的显示宽度不超过指定限制。

    Args:
        line: 待截断的文本行。
        max_width: 最大显示宽度（列数）。

    Returns:
        截断后的文本，超出部分以省略号替代。
    """
    from rich.cells import cell_len

    if cell_len(line) <= max_width:
        return line
    ellipsis_width = cell_len(_ELLIPSIS)
    budget = max_width - ellipsis_width
    width = 0
    for i, ch in enumerate(line):
        width += cell_len(ch)
        if width > budget:
            return line[:i] + _ELLIPSIS
    return line


# 增量令牌提交用的 markdown-it 解析器（延迟初始化）
_md_parser: MarkdownIt | None = None


def _get_md_parser() -> MarkdownIt:
    """获取或初始化 markdown-it 解析器。

    解析器配置与渲染路径（utils/rich/markdown.py）使用的扩展一致，
    以确保块边界检测的一致性。

    Returns:
        配置好的 MarkdownIt 解析器实例。
    """
    global _md_parser
    if _md_parser is None:
        from markdown_it import MarkdownIt

        # 匹配渲染路径使用的扩展，确保块边界检测一致
        _md_parser = MarkdownIt().enable("strikethrough").enable("table")
    return _md_parser


def _estimate_tokens(text: str) -> float:
    """估算混合 CJK/拉丁文本的令牌数。

    返回浮点数以便调用者跨小块累积时避免每块的整数截断
    （例如 3 字符 ASCII 块立即截断为 int 会得到 0，但作为 float 为 0.75）。

    基于 BPE 令牌器（cl100k、o200k）的启发式规则：
    - CJK 字符：约 1.5 令牌/字符（常拆分为 2 字节片段）
    - 拉丁/ASCII：约 1 令牌/4 字符（单词平均约 4 字符）

    Args:
        text: 待估算令牌数的文本。

    Returns:
        估算的令牌数（浮点数）。
    """
    cjk = 0
    other = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF  # CJK 统一汉字
            or 0x3400 <= cp <= 0x4DBF  # CJK 扩展 A
            or 0xF900 <= cp <= 0xFAFF  # CJK 兼容汉字
            or 0x3000 <= cp <= 0x303F  # CJK 符号和标点
            or 0xFF00 <= cp <= 0xFFEF  # 全角形式
        ):
            cjk += 1
        else:
            other += 1
    return cjk * 1.5 + other / 4


def _find_committed_boundary(text: str) -> int | None:
    """查找可安全提交的字符边界位置。

    使用增量令牌提交算法：通过 markdown-it-py 将文本解析为块级令牌，
    确认除最后一个块之外的所有块（最后一个块可能因流式截断而不完整）。

    Args:
        text: 待分析的 Markdown 文本。

    Returns:
        可安全提交的字符偏移量，若少于 2 个块则返回 None。
    """
    md = _get_md_parser()
    tokens = md.parse(text)

    # 仅收集顶层块边界，通过跟踪嵌套深度实现
    # 嵌套令牌（如 bullet_list_open 内的 list_item_open）不应视为独立块
    # 否则列表和引用块会被错误拆分
    block_maps: list[list[int]] = []
    depth = 0
    for t in tokens:
        if t.nesting == 1:
            if depth == 0 and t.map is not None:
                block_maps.append(t.map)
            depth += 1
        elif t.nesting == -1:
            depth -= 1
        elif depth == 0 and t.type in _SELF_CLOSING_BLOCKS and t.map is not None:
            block_maps.append(t.map)

    if len(block_maps) < 2:
        return None

    # 通过扫描换行符将结束行号转换为字符偏移量
    target_line = block_maps[-2][1]
    offset = 0
    for _ in range(target_line):
        offset = text.index("\n", offset) + 1
    return offset


def _tail_lines(text: str, n: int) -> str:
    """提取文本的最后 n 行。

    通过反向扫描实现 O(n) 时间复杂度。

    Args:
        text: 待提取的文本。
        n: 要提取的行数。

    Returns:
        文本最后 n 行的内容。
    """
    pos = len(text)
    for _ in range(n):
        pos = text.rfind("\n", 0, pos)
        if pos == -1:
            return text
    return text[pos + 1 :]


class _ContentBlock:
    """流式内容块，支持增量 Markdown 提交。

    对于撰写模式（is_think=False），已确认的 Markdown 块通过 console.print()
    永久输出到终端，提供实时流式输出体验。仅未确认的尾部保留在瞬态 Rich Live 区域。

    对于思考模式（is_think=True），内容保持在 Live 区域作为滚动预览，
    直至块结束。

    Attributes:
        is_think: 是否为思考内容块。
        _spinner: 加载动画组件。
        raw_text: 原始文本内容。
        _token_count: 累积的令牌估算值（浮点数）。
        _start_time: 块开始时间。
        _committed_len: 已提交的文本长度（仅撰写模式）。
        _has_printed_bullet: 是否已打印项目符号。
    """

    def __init__(self, is_think: bool):
        """初始化内容块。

        Args:
            is_think: 是否为思考内容块。
        """
        self.is_think = is_think
        self._spinner = Spinner("dots", "")
        self.raw_text = ""
        # 累积的浮点估算值，避免每块整数截断
        self._token_count: float = 0.0
        self._start_time = time.monotonic()
        # 增量提交状态（仅撰写模式）
        self._committed_len = 0
        self._has_printed_bullet = False

    # -- 公共 API ----------------------------------------------------------

    def append(self, content: str) -> None:
        """追加内容到块。

        Args:
            content: 要追加的文本内容。
        """
        self.raw_text += content
        self._token_count += _estimate_tokens(content)
        # 块边界需要换行符，跳过行内片段的解析
        if not self.is_think and "\n" in content:
            self._flush_committed()

    def compose(self) -> RenderableType:
        """渲染瞬态 Live 区域内容。

        Returns:
            可渲染的内容对象。
        """
        pending = self._pending_text()

        # 思考模式：始终显示加载动画 + 预览
        if self.is_think:
            spinner = self._compose_spinner()
            if not pending:
                return spinner
            preview = self._build_preview(pending)
            return Group(spinner, Text(preview, style="grey50 italic"))

        # 撰写模式：始终显示带耗时和令牌数的加载动画
        # 已确认块已永久打印在上方
        return self._compose_spinner()

    def compose_final(self) -> RenderableType:
        """渲染块结束时的剩余未提交内容。

        Returns:
            可渲染的最终内容对象。
        """
        remaining = self._pending_text()
        if not remaining:
            return Text("")
        if self.is_think:
            return BulletColumns(
                Markdown(remaining, style="grey50 italic"),
                bullet_style="grey50",
            )
        return self._wrap_bullet(Markdown(remaining))

    def has_pending(self) -> bool:
        """检查是否有未提交内容。

        Returns:
            是否有未提交内容需要刷新。
        """
        return bool(self._pending_text())

    # -- 私有方法 ----------------------------------------------------------

    def _pending_text(self) -> str:
        """获取待处理的文本内容。

        Returns:
            未提交的文本内容。
        """
        return self.raw_text[self._committed_len :]

    def _wrap_bullet(self, renderable: RenderableType) -> BulletColumns:
        """包装为项目符号列。

        第一次调用使用项目符号，后续调用使用空格。

        Args:
            renderable: 待包装的可渲染对象。

        Returns:
            包装后的 BulletColumns 对象。
        """
        if self._has_printed_bullet:
            return BulletColumns(renderable, bullet=Text(" "))
        self._has_printed_bullet = True
        return BulletColumns(renderable)

    def _flush_committed(self) -> None:
        """将已确认的 Markdown 块提交到永久终端输出。"""
        pending = self._pending_text()
        if not pending:
            return
        boundary = _find_committed_boundary(pending)
        if boundary is None:
            return
        committed_text = pending[:boundary]
        console.print(self._wrap_bullet(Markdown(committed_text)))
        self._committed_len += boundary

    def _compose_spinner(self) -> Spinner:
        """构建加载动画。

        Returns:
            配置好的 Spinner 对象。
        """
        elapsed = time.monotonic() - self._start_time
        label = "Thinking..." if self.is_think else "Composing..."
        elapsed_str = f"{int(elapsed)}s" if elapsed >= 1 else "<1s"
        count_str = f"{format_token_count(int(self._token_count))} tokens"

        self._spinner.text = Text.assemble(
            (label, ""),
            (f" {elapsed_str}", "grey50"),
            (f" · {count_str}", "grey50"),
        )
        return self._spinner

    def _build_preview(self, text: str) -> str:
        """构建预览文本。

        Args:
            text: 源文本内容。

        Returns:
            截断后的预览文本。
        """
        max_lines = _THINKING_PREVIEW_LINES if self.is_think else _PENDING_PREVIEW_LINES
        max_width = console.width - 2 if console.width else 78
        tail_text = _tail_lines(text, max_lines)
        lines = tail_text.split("\n")
        return "\n".join(_truncate_to_display_width(line, max_width) for line in lines)


class _ToolCallBlock:
    """工具调用块，展示工具执行状态和结果。

    用于渲染智能体的工具调用过程，包括工具名称、参数摘要、
    执行结果以及子智能体的工具调用追踪。

    Attributes:
        FinishedSubCall: 已完成的子工具调用记录（NamedTuple）。
        _tool_name: 工具名称。
        _lexer: 流式 JSON 词法分析器。
        _argument: 提取的关键参数摘要。
        _full_url: 从 FetchURL 工具参数中提取的完整 URL。
        _result: 工具返回值。
        _subagent_id: 子智能体 ID。
        _subagent_type: 子智能体类型。
        _ongoing_subagent_tool_calls: 正在执行的子工具调用映射。
        _last_subagent_tool_call: 最后接收到的子工具调用。
        _n_finished_subagent_tool_calls: 已完成的子工具调用总数。
        _finished_subagent_tool_calls: 已完成的子工具调用列表。
        _spinning_dots: 加载动画组件。
        _renderable: 当前渲染内容。
    """

    class FinishedSubCall(NamedTuple):
        """已完成的子工具调用记录。

        Attributes:
            call: 工具调用对象。
            result: 工具返回值。
        """

        call: ToolCall
        result: ToolReturnValue

    def __init__(self, tool_call: ToolCall):
        """初始化工具调用块。

        Args:
            tool_call: 工具调用对象。
        """
        self._tool_name = tool_call.function.name
        self._lexer = streamingjson.Lexer()
        if tool_call.function.arguments is not None:
            self._lexer.append_string(tool_call.function.arguments)

        self._argument = extract_key_argument(self._lexer, self._tool_name)
        self._full_url = self._extract_full_url(tool_call.function.arguments, self._tool_name)
        self._result: ToolReturnValue | None = None
        self._subagent_id: str | None = None
        self._subagent_type: str | None = None

        self._ongoing_subagent_tool_calls: dict[str, ToolCall] = {}
        self._last_subagent_tool_call: ToolCall | None = None
        self._n_finished_subagent_tool_calls = 0
        self._finished_subagent_tool_calls = deque[_ToolCallBlock.FinishedSubCall](
            maxlen=MAX_SUBAGENT_TOOL_CALLS_TO_SHOW
        )

        self._spinning_dots = Spinner("dots", text="")
        self._renderable: RenderableType = self._compose()

    def compose(self) -> RenderableType:
        """获取当前渲染内容。

        Returns:
            可渲染的内容对象。
        """
        return self._renderable

    @property
    def finished(self) -> bool:
        """检查工具调用是否已完成。

        Returns:
            是否已完成。
        """
        return self._result is not None

    def append_args_part(self, args_part: str):
        """追加工具参数片段。

        Args:
            args_part: 参数片段字符串。
        """
        if self.finished:
            return
        self._lexer.append_string(args_part)
        # TODO: 若参数已稳定，可能无需提取详情
        argument = extract_key_argument(self._lexer, self._tool_name)
        if argument and argument != self._argument:
            self._argument = argument
            self._full_url = self._extract_full_url(self._lexer.complete_json(), self._tool_name)
            self._renderable = BulletColumns(
                self._build_headline_text(),
                bullet=self._spinning_dots,
            )

    def finish(self, result: ToolReturnValue):
        """完成工具调用。

        Args:
            result: 工具返回值。
        """
        self._result = result
        self._renderable = self._compose()

    def append_sub_tool_call(self, tool_call: ToolCall):
        """追加子工具调用。

        Args:
            tool_call: 子工具调用对象。
        """
        self._ongoing_subagent_tool_calls[tool_call.id] = tool_call
        self._last_subagent_tool_call = tool_call

    def append_sub_tool_call_part(self, tool_call_part: ToolCallPart):
        """追加子工具调用参数片段。

        Args:
            tool_call_part: 工具调用参数片段。
        """
        if self._last_subagent_tool_call is None:
            return
        if not tool_call_part.arguments_part:
            return
        if self._last_subagent_tool_call.function.arguments is None:
            self._last_subagent_tool_call.function.arguments = tool_call_part.arguments_part
        else:
            self._last_subagent_tool_call.function.arguments += tool_call_part.arguments_part

    def finish_sub_tool_call(self, tool_result: ToolResult):
        """完成子工具调用。

        Args:
            tool_result: 工具执行结果。
        """
        self._last_subagent_tool_call = None
        sub_tool_call = self._ongoing_subagent_tool_calls.pop(tool_result.tool_call_id, None)
        if sub_tool_call is None:
            return

        self._finished_subagent_tool_calls.append(
            _ToolCallBlock.FinishedSubCall(
                call=sub_tool_call,
                result=tool_result.return_value,
            )
        )
        self._n_finished_subagent_tool_calls += 1
        self._renderable = self._compose()

    def set_subagent_metadata(self, agent_id: str, subagent_type: str) -> None:
        """设置子智能体元数据。

        Args:
            agent_id: 子智能体 ID。
            subagent_type: 子智能体类型。
        """
        changed = (self._subagent_id, self._subagent_type) != (agent_id, subagent_type)
        self._subagent_id = agent_id
        self._subagent_type = subagent_type
        if changed:
            self._renderable = self._compose()

    def _compose(self) -> RenderableType:
        """构建工具调用块的渲染内容。

        Returns:
            可渲染的内容对象。
        """
        lines: list[RenderableType] = [
            self._build_headline_text(),
        ]
        if self._subagent_id is not None and self._subagent_type is not None:
            lines.append(
                BulletColumns(
                    Text(
                        f"subagent {self._subagent_type} ({self._subagent_id})",
                        style="grey50",
                    ),
                    bullet_style="grey50",
                )
            )

        if self._n_finished_subagent_tool_calls > MAX_SUBAGENT_TOOL_CALLS_TO_SHOW:
            n_hidden = self._n_finished_subagent_tool_calls - MAX_SUBAGENT_TOOL_CALLS_TO_SHOW
            lines.append(
                BulletColumns(
                    Text(
                        f"{n_hidden} more tool call{'s' if n_hidden > 1 else ''} ...",
                        style="grey50 italic",
                    ),
                    bullet_style="grey50",
                )
            )
        for sub_call, sub_result in self._finished_subagent_tool_calls:
            argument = extract_key_argument(
                sub_call.function.arguments or "", sub_call.function.name
            )
            sub_url = self._extract_full_url(sub_call.function.arguments, sub_call.function.name)
            sub_text = Text()
            sub_text.append("Used ")
            sub_text.append(sub_call.function.name, style="blue")
            if argument:
                sub_text.append(" (", style="grey50")
                arg_style = Style(color="grey50", link=sub_url) if sub_url else "grey50"
                sub_text.append(argument, style=arg_style)
                sub_text.append(")", style="grey50")
            lines.append(
                BulletColumns(
                    sub_text,
                    bullet_style="green" if not sub_result.is_error else "dark_red",
                )
            )

        if self._result is not None:
            display = self._result.display
            idx = 0
            while idx < len(display):
                block = display[idx]
                if isinstance(block, DiffDisplayBlock):
                    # 收集连续的同文件差异块
                    path = block.path
                    diff_blocks: list[DiffDisplayBlock] = []
                    while idx < len(display):
                        b = display[idx]
                        if not isinstance(b, DiffDisplayBlock) or b.path != path:
                            break
                        diff_blocks.append(b)
                        idx += 1
                    if any(b.is_summary for b in diff_blocks):
                        lines.append(render_diff_summary_panel(path, diff_blocks))
                    else:
                        hunks, added_total, removed_total = collect_diff_hunks(diff_blocks)
                        if hunks:
                            lines.append(render_diff_panel(path, hunks, added_total, removed_total))
                elif isinstance(block, BriefDisplayBlock):
                    style = "grey50" if not self._result.is_error else "dark_red"
                    if block.text:
                        lines.append(Markdown(block.text, style=style))
                    idx += 1
                elif isinstance(block, TodoDisplayBlock):
                    markdown = self._render_todo_markdown(block)
                    if markdown:
                        lines.append(Markdown(markdown, style="grey50"))
                    idx += 1
                elif isinstance(block, BackgroundTaskDisplayBlock):
                    lines.append(
                        Markdown(
                            (f"`{block.task_id}` [{block.status}] {block.description}"),
                            style="grey50",
                        )
                    )
                    idx += 1
                else:
                    idx += 1

        if self.finished:
            assert self._result is not None
            return BulletColumns(
                Group(*lines),
                bullet_style="green" if not self._result.is_error else "dark_red",
            )
        else:
            return BulletColumns(
                Group(*lines),
                bullet=self._spinning_dots,
            )

    @staticmethod
    def _extract_full_url(arguments: str | None, tool_name: str) -> str | None:
        """从 FetchURL 工具参数中提取完整 URL。

        Args:
            arguments: 工具参数 JSON 字符串。
            tool_name: 工具名称。

        Returns:
            提取的 URL 字符串，若非 FetchURL 工具或无 URL 则返回 None。
        """
        if tool_name != "FetchURL" or not arguments:
            return None
        try:
            args = json.loads(arguments, strict=False)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(args, dict):
            url = cast(dict[str, Any], args).get("url")
            if url:
                return str(url)
        return None

    def _build_headline_text(self) -> Text:
        """构建工具调用的标题文本。

        Returns:
            包含工具名称和参数摘要的 Text 对象。
        """
        text = Text()
        text.append("Used " if self.finished else "Using ")
        text.append(self._tool_name, style="blue")
        if self._argument:
            text.append(" (", style="grey50")
            arg_style = Style(color="grey50", link=self._full_url) if self._full_url else "grey50"
            text.append(self._argument, style=arg_style)
            text.append(")", style="grey50")
        return text

    def _render_todo_markdown(self, block: TodoDisplayBlock) -> str:
        """将 Todo 显示块渲染为 Markdown 格式。

        Args:
            block: Todo 显示块对象。

        Returns:
            Markdown 格式的字符串。
        """
        lines: list[str] = []
        for todo in block.items:
            normalized = todo.status.replace("_", " ").lower()
            match normalized:
                case "pending":
                    lines.append(f"- {todo.title}")
                case "in progress":
                    lines.append(f"- {todo.title} ←")
                case "done":
                    lines.append(f"- ~~{todo.title}~~")
                case _:
                    lines.append(f"- {todo.title}")
        return "\n".join(lines)


class _NotificationBlock:
    """通知消息块，展示系统通知。

    用于渲染不同严重级别的通知消息，包括信息、成功、警告和错误。

    Attributes:
        _SEVERITY_STYLE: 严重级别对应的样式映射。
        notification: 通知对象。
    """

    _SEVERITY_STYLE = {
        "info": "cyan",
        "success": "green",
        "warning": "yellow",
        "error": "red",
    }

    def __init__(self, notification: Notification):
        """初始化通知块。

        Args:
            notification: 通知对象。
        """
        self.notification = notification

    def compose(self) -> RenderableType:
        """构建通知块的渲染内容。

        Returns:
            可渲染的内容对象。
        """
        style = self._SEVERITY_STYLE.get(self.notification.severity, "cyan")
        lines: list[RenderableType] = [Text(self.notification.title, style=f"bold {style}")]
        body = self.notification.body.strip()
        if body:
            body_lines = body.splitlines()
            preview = "\n".join(body_lines[:2])
            if len(body_lines) > 2:
                preview += "\n..."
            lines.append(Text(preview, style="grey50"))
        return BulletColumns(Group(*lines), bullet_style=style)


class _StatusBlock:
    """状态信息块，展示上下文使用状态。

    用于在界面底部显示智能体的上下文使用情况。

    Attributes:
        text: 状态文本对象。
        _context_usage: 上下文使用百分比。
        _context_tokens: 当前上下文令牌数。
        _max_context_tokens: 最大上下文令牌数。
    """

    def __init__(self, initial: StatusUpdate) -> None:
        """初始化状态块。

        Args:
            initial: 初始状态更新对象。
        """
        self.text = Text("", justify="right")
        self._context_usage: float = 0.0
        self._context_tokens: int = 0
        self._max_context_tokens: int = 0
        self.update(initial)

    def render(self) -> RenderableType:
        """渲染状态文本。

        Returns:
            可渲染的状态文本对象。
        """
        return self.text

    def update(self, status: StatusUpdate) -> None:
        """更新状态信息。

        Args:
            status: 状态更新对象。
        """
        if status.context_usage is not None:
            self._context_usage = status.context_usage
        if status.context_tokens is not None:
            self._context_tokens = status.context_tokens
        if status.max_context_tokens is not None:
            self._max_context_tokens = status.max_context_tokens
        if status.context_usage is not None:
            self.text.plain = format_context_status(
                self._context_usage,
                self._context_tokens,
                self._max_context_tokens,
            )


@asynccontextmanager
async def _keyboard_listener(
    handler: Callable[[KeyboardListener, KeyEvent], Awaitable[None]],
):
    """键盘监听器异步上下文管理器。

    创建键盘监听器并在上下文结束时自动停止。

    Args:
        handler: 键盘事件处理回调函数。

    Yields:
        进入上下文时的空值。
    """
    listener = KeyboardListener()
    await listener.start()

    async def _keyboard():
        while True:
            event = await listener.get()
            await handler(listener, event)

    task = asyncio.create_task(_keyboard())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await listener.stop()


class _LiveView:
    """基础实时视图类。

    实现智能体行为的实时可视化，包括内容块、工具调用、审批请求、
    问答面板和通知消息的渲染与交互。

    Attributes:
        _cancel_event: 取消事件。
        _mooning_spinner: 月相加载动画（等待状态）。
        _compacting_spinner: 压缩加载动画。
        _mcp_loading_spinner: MCP 连接加载动画。
        _current_content_block: 当前内容块。
        _tool_call_blocks: 工具调用块映射。
        _last_tool_call_block: 最后的工具调用块。
        _approval_request_queue: 审批请求队列。
        _current_approval_request_panel: 当前审批面板。
        _question_request_queue: 问答请求队列。
        _current_question_panel: 当前问答面板。
        _notification_blocks: 通知消息块队列。
        _live_notification_blocks: 实时显示的通知块。
        _status_block: 状态信息块。
        _need_recompose: 是否需要重新渲染。
        _external_messages: 外部消息队列。
    """

    def __init__(self, initial_status: StatusUpdate, cancel_event: asyncio.Event | None = None):
        """初始化实时视图。

        Args:
            initial_status: 初始状态更新对象。
            cancel_event: 取消事件。
        """
        self._cancel_event = cancel_event

        self._mooning_spinner: Spinner | None = None
        self._compacting_spinner: Spinner | None = None
        self._mcp_loading_spinner: Spinner | None = None

        self._current_content_block: _ContentBlock | None = None
        self._tool_call_blocks: dict[str, _ToolCallBlock] = {}
        self._last_tool_call_block: _ToolCallBlock | None = None
        self._approval_request_queue = deque[ApprovalRequest]()
        """
        可能存在多个子智能体同时请求审批的情况，
        此时需要将它们排队并逐一显示。
        """
        self._current_approval_request_panel: ApprovalRequestPanel | None = None
        self._question_request_queue = deque[QuestionRequest]()
        self._current_question_panel: QuestionRequestPanel | None = None
        self._notification_blocks = deque[_NotificationBlock]()
        self._live_notification_blocks = deque[_NotificationBlock](maxlen=MAX_LIVE_NOTIFICATIONS)
        self._status_block = _StatusBlock(initial_status)

        self._need_recompose = False
        self._external_messages: Queue[WireMessage] = Queue()

    def _reset_live_shape(self, live: Live) -> None:
        """重置 Live 渲染器的缓存高度。

        Rich 未提供清除 Live 缓存渲染高度的公共 API。
        退出 pager 后，陈旧的高度会导致光标恢复跳跃，
        因此重置私有 _shape 以便下次刷新重新锚定。

        Args:
            live: Rich Live 对象。
        """
        live._live_render._shape = None  # type: ignore[reportPrivateUsage]

    async def _drain_external_message_after_wire_shutdown(
        self,
        external_task: asyncio.Task[WireMessage],
    ) -> tuple[WireMessage | None, asyncio.Task[WireMessage]]:
        """在 Wire 关闭后排空外部消息队列。

        Args:
            external_task: 外部消息获取任务。

        Returns:
            元组：(获取到的消息或 None，新的外部消息获取任务)。
        """
        try:
            msg = await asyncio.wait_for(
                asyncio.shield(external_task),
                timeout=EXTERNAL_MESSAGE_GRACE_S,
            )
        except (TimeoutError, QueueShutDown):
            return None, external_task
        return msg, asyncio.create_task(self._external_messages.get())

    async def visualize_loop(self, wire: WireUISide):
        """可视化主循环。

        Args:
            wire: Wire 通信通道。
        """
        with Live(
            self.compose(),
            console=console,
            refresh_per_second=10,
            transient=True,
            vertical_overflow="visible",
        ) as live:

            async def keyboard_handler(listener: KeyboardListener, event: KeyEvent) -> None:
                """处理键盘事件。

                Args:
                    listener: 键盘监听器。
                    event: 键盘事件。
                """
                # 特殊处理 Ctrl+E - 在 pager 激活时暂停 Live
                if event == KeyEvent.CTRL_E:
                    if self.has_expandable_panel():
                        await listener.pause()
                        live.stop()
                        try:
                            self._show_expandable_panel_content()
                        finally:
                            # 重置 live 渲染形状以便下次刷新重新锚定
                            self._reset_live_shape(live)
                            live.start()
                            live.update(self.compose(), refresh=True)
                            await listener.resume()
                    return

                # 在问答面板选中"其他"选项时处理 ENTER/SPACE
                if self._should_prompt_question_other_for_key(event):
                    panel = self._current_question_panel
                    assert panel is not None
                    question_text = panel.current_question_text
                    await listener.pause()
                    live.stop()
                    try:
                        text = await prompt_other_input(question_text)
                    finally:
                        self._reset_live_shape(live)
                        live.start()
                        await listener.resume()

                    self._submit_question_other_text(text)
                    live.update(self.compose(), refresh=True)
                    return

                self.dispatch_keyboard_event(event)
                if self._need_recompose:
                    live.update(self.compose(), refresh=True)
                    self._need_recompose = False

            async with _keyboard_listener(keyboard_handler):
                wire_task = asyncio.create_task(wire.receive())
                external_task = asyncio.create_task(self._external_messages.get())
                while True:
                    try:
                        done, _ = await asyncio.wait(
                            [wire_task, external_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if wire_task in done:
                            msg = wire_task.result()
                            wire_task = asyncio.create_task(wire.receive())
                        else:
                            msg = external_task.result()
                            external_task = asyncio.create_task(self._external_messages.get())
                    except QueueShutDown:
                        msg, external_task = await self._drain_external_message_after_wire_shutdown(
                            external_task
                        )
                        if msg is not None:
                            self.dispatch_wire_message(msg)
                            if self._need_recompose:
                                live.update(self.compose(), refresh=True)
                                self._need_recompose = False
                            continue
                        self.cleanup(is_interrupt=False)
                        live.update(self.compose(), refresh=True)
                        break

                    if isinstance(msg, StepInterrupted):
                        self.cleanup(is_interrupt=True)
                        live.update(self.compose(), refresh=True)
                        break

                    self.dispatch_wire_message(msg)
                    if self._need_recompose:
                        live.update(self.compose(), refresh=True)
                        self._need_recompose = False
                wire_task.cancel()
                external_task.cancel()
                self._external_messages.shutdown(immediate=True)
                with suppress(asyncio.CancelledError, QueueShutDown):
                    await wire_task
                with suppress(asyncio.CancelledError, QueueShutDown):
                    await external_task

    def refresh_soon(self) -> None:
        """标记需要重新渲染。"""
        self._need_recompose = True

    def _on_question_panel_state_changed(self) -> None:
        """问答面板状态变化的钩子方法，供子类重写。

        Returns:
            None。
        """
        return None

    def enqueue_external_message(self, msg: WireMessage) -> None:
        """将外部消息加入队列。

        Args:
            msg: Wire 消息对象。
        """
        try:
            self._external_messages.put_nowait(msg)
        except QueueShutDown:
            logger.debug("Ignoring external wire message after live view shutdown: {msg}", msg=msg)

    def has_expandable_panel(self) -> bool:
        """检查是否有可展开的面板。

        Returns:
            是否有审批或问答面板可展开。
        """
        return (
            self._expandable_approval_panel() is not None
            or self._expandable_question_panel() is not None
        )

    def _expandable_approval_panel(self) -> ApprovalRequestPanel | None:
        """获取可展开的审批面板。

        Returns:
            可展开的审批面板，若无则返回 None。
        """
        panel = self._current_approval_request_panel
        if panel is not None and panel.has_expandable_content:
            return panel
        return None

    def _expandable_question_panel(self) -> QuestionRequestPanel | None:
        """获取可展开的问答面板。

        Returns:
            可展开的问答面板，若无则返回 None。
        """
        panel = self._current_question_panel
        if panel is not None and panel.has_expandable_content:
            return panel
        return None

    def _show_expandable_panel_content(self) -> bool:
        """在 pager 中显示可展开面板内容。

        Returns:
            是否成功显示。
        """
        if approval_panel := self._expandable_approval_panel():
            show_approval_in_pager(approval_panel)
            return True
        if question_panel := self._expandable_question_panel():
            show_question_body_in_pager(question_panel)
            return True
        return False

    def _should_prompt_question_other_for_key(self, key: KeyEvent) -> bool:
        """检查是否应针对按键提示问答面板的"其他"输入。

        Args:
            key: 键盘事件。

        Returns:
            是否应提示其他输入。
        """
        panel = self._current_question_panel
        if panel is None or not panel.should_prompt_other_input():
            return False
        return key == KeyEvent.ENTER or (key == KeyEvent.SPACE and not panel.is_multi_select)

    def _submit_question_other_text(self, text: str) -> None:
        """提交问答面板的"其他"文本输入。

        Args:
            text: 用户输入的文本。
        """
        panel = self._current_question_panel
        if panel is None:
            return

        all_done = panel.submit_other(text)
        if all_done:
            panel.request.resolve(panel.get_answers())
            self.show_next_question_request()
        self.refresh_soon()

    def compose(self, *, include_status: bool = True) -> RenderableType:
        """构建实时视图的显示内容。

        审批和问答面板优先渲染，确保它们始终位于终端顶部，
        即使工具调用输出足够长将内容推出可见区域。

        Args:
            include_status: 是否包含状态信息。

        Returns:
            可渲染的内容对象。
        """
        blocks: list[RenderableType] = []
        # 审批/问答面板优先 — 最高视觉优先级
        if self._current_approval_request_panel:
            blocks.append(self._current_approval_request_panel.render())
        if self._current_question_panel:
            blocks.append(self._current_question_panel.render())
        # 加载动画或内容 + 工具调用
        if self._mcp_loading_spinner is not None:
            blocks.append(self._mcp_loading_spinner)
        elif self._mooning_spinner is not None:
            blocks.append(self._mooning_spinner)
        elif self._compacting_spinner is not None:
            blocks.append(self._compacting_spinner)
        else:
            if self._current_content_block is not None:
                blocks.append(self._current_content_block.compose())
            for tool_call in self._tool_call_blocks.values():
                blocks.append(tool_call.compose())
        for notification in self._live_notification_blocks:
            blocks.append(notification.compose())

        if include_status:
            blocks.append(self._status_block.render())
        return Group(*blocks)

    def dispatch_wire_message(self, msg: WireMessage) -> None:
        """将 Wire 消息分发到 UI 组件。

        Args:
            msg: Wire 消息对象。

        Raises:
            AssertionError: 若收到 StepInterrupted 消息（应在 visualize_loop 中处理）。
        """
        assert not isinstance(msg, StepInterrupted)  # 已在 visualize_loop 中处理

        if isinstance(msg, StepBegin):
            self.cleanup(is_interrupt=False)
            self._mcp_loading_spinner = None
            self._mooning_spinner = Spinner("moon", "")
            self.refresh_soon()
            return

        if self._mooning_spinner is not None:
            # 除 StepBegin 外的任何消息都应结束月相等待状态
            self._mooning_spinner = None
            self.refresh_soon()

        match msg:
            case TurnBegin():
                self.flush_content()
            case SteerInput(user_input=user_input):
                self.cleanup(is_interrupt=False)
                content: list[ContentPart]
                if isinstance(user_input, list):
                    content = list(user_input)
                else:
                    content = [TextPart(text=user_input)]
                console.print(render_user_echo(Message(role="user", content=content)))
            case TurnEnd():
                pass
            case CompactionBegin():
                self._compacting_spinner = Spinner("balloon", "Compacting...")
                self.refresh_soon()
            case CompactionEnd():
                self._compacting_spinner = None
                self.refresh_soon()
            case MCPLoadingBegin():
                self._mcp_loading_spinner = Spinner("dots", "Connecting to MCP servers...")
                self.refresh_soon()
            case MCPLoadingEnd():
                self._mcp_loading_spinner = None
                self.refresh_soon()
            case StatusUpdate():
                self._status_block.update(msg)
            case Notification():
                self.append_notification(msg)
            case ContentPart():
                self.append_content(msg)
            case ToolCall():
                self.append_tool_call(msg)
            case ToolCallPart():
                self.append_tool_call_part(msg)
            case ToolResult():
                self.append_tool_result(msg)
            case ApprovalResponse():
                self._reconcile_approval_requests()
            case SubagentEvent():
                self.handle_subagent_event(msg)
            case PlanDisplay():
                self.display_plan(msg)
            case ApprovalRequest():
                self.request_approval(msg)
            case QuestionRequest():
                self.request_question(msg)
            case ToolCallRequest():
                logger.warning("Unexpected ToolCallRequest in shell UI: {msg}", msg=msg)
            case _:
                pass

    def _try_submit_question(self) -> None:
        """提交当前问答答案；若全部完成则解析并推进。"""
        panel = self._current_question_panel
        if panel is None:
            return
        all_done = panel.submit()
        if all_done:
            panel.request.resolve(panel.get_answers())
            self.show_next_question_request()

    def dispatch_keyboard_event(self, event: KeyEvent) -> None:
        """处理键盘事件分发。

        Args:
            event: 键盘事件。
        """
        # 处理问答面板键盘事件
        if self._current_question_panel is not None:
            match event:
                case KeyEvent.UP:
                    self._current_question_panel.move_up()
                case KeyEvent.DOWN:
                    self._current_question_panel.move_down()
                case KeyEvent.LEFT:
                    self._current_question_panel.prev_tab()
                case KeyEvent.RIGHT | KeyEvent.TAB:
                    self._current_question_panel.next_tab()
                case KeyEvent.SPACE:
                    if self._current_question_panel.is_multi_select:
                        self._current_question_panel.toggle_select()
                    else:
                        self._try_submit_question()
                case KeyEvent.ENTER:
                    # "其他"选项在 keyboard_handler 中处理（异步上下文）
                    self._try_submit_question()
                case KeyEvent.ESCAPE:
                    self._current_question_panel.request.resolve({})
                    self.show_next_question_request()
                case (
                    KeyEvent.NUM_1
                    | KeyEvent.NUM_2
                    | KeyEvent.NUM_3
                    | KeyEvent.NUM_4
                    | KeyEvent.NUM_5
                    | KeyEvent.NUM_6
                ):
                    # 数字键在问答面板中选择选项
                    num_map = {
                        KeyEvent.NUM_1: 0,
                        KeyEvent.NUM_2: 1,
                        KeyEvent.NUM_3: 2,
                        KeyEvent.NUM_4: 3,
                        KeyEvent.NUM_5: 4,
                        KeyEvent.NUM_6: 5,
                    }
                    idx = num_map[event]
                    panel = self._current_question_panel
                    if panel.select_index(idx):
                        if panel.is_multi_select:
                            panel.toggle_select()
                        elif not panel.is_other_selected:
                            # 单选自动提交（除非选中"其他")
                            self._try_submit_question()
                case _:
                    pass
            self.refresh_soon()
            return

        # 处理 ESC 键取消运行
        if event == KeyEvent.ESCAPE and self._cancel_event is not None:
            self._cancel_event.set()
            return

        # 处理审批面板键盘事件
        if self._current_approval_request_panel is not None:
            match event:
                case KeyEvent.UP:
                    self._current_approval_request_panel.move_up()
                    self.refresh_soon()
                case KeyEvent.DOWN:
                    self._current_approval_request_panel.move_down()
                    self.refresh_soon()
                case KeyEvent.ENTER:
                    self._submit_approval()
                case KeyEvent.NUM_1 | KeyEvent.NUM_2 | KeyEvent.NUM_3 | KeyEvent.NUM_4:
                    # 数字键直接选择并提交审批选项
                    num_map = {
                        KeyEvent.NUM_1: 0,
                        KeyEvent.NUM_2: 1,
                        KeyEvent.NUM_3: 2,
                        KeyEvent.NUM_4: 3,
                    }
                    idx = num_map[event]
                    if idx < len(self._current_approval_request_panel.options):
                        self._current_approval_request_panel.selected_index = idx
                        self._submit_approval()
                case _:
                    pass
            return

    def _submit_approval(self) -> None:
        """提交当前选中的审批响应。"""
        assert self._current_approval_request_panel is not None
        request = self._current_approval_request_panel.request
        resp = self._current_approval_request_panel.get_selected_response()
        request.resolve(resp)
        if resp == "approve_for_session":
            to_remove_from_queue: list[ApprovalRequest] = []
            for request in self._approval_request_queue:
                # 批准所有具有相同动作的排队请求
                if request.action == self._current_approval_request_panel.request.action:
                    request.resolve("approve_for_session")
                    to_remove_from_queue.append(request)
            for request in to_remove_from_queue:
                self._approval_request_queue.remove(request)
        self.show_next_approval_request()

    def cleanup(self, is_interrupt: bool) -> None:
        """清理实时视图（步骤结束或中断时）。

        Args:
            is_interrupt: 是否为中断导致的清理。
        """
        self.flush_content()

        for block in self._tool_call_blocks.values():
            if not block.finished:
                # 此情况不应发生，但以防万一
                block.finish(
                    ToolError(message="", brief="Interrupted")
                    if is_interrupt
                    else ToolOk(output="")
                )
        self._last_tool_call_block = None
        self.flush_finished_tool_calls()
        self.flush_notifications()

        while self._approval_request_queue:
            # 此情况不应发生，但以防万一
            self._approval_request_queue.popleft().resolve("reject")
        self._current_approval_request_panel = None

        while self._question_request_queue:
            self._question_request_queue.popleft().resolve({})
        self._current_question_panel = None

    def flush_content(self) -> None:
        """刷新当前内容块。"""
        if self._current_content_block is not None:
            if self._current_content_block.has_pending():
                console.print(self._current_content_block.compose_final())
            self._current_content_block = None
            self.refresh_soon()

    def flush_finished_tool_calls(self) -> None:
        """刷新所有已完成的工具调用块。"""
        tool_call_ids = list(self._tool_call_blocks.keys())
        for tool_call_id in tool_call_ids:
            block = self._tool_call_blocks[tool_call_id]
            if not block.finished:
                break

            self._tool_call_blocks.pop(tool_call_id)
            console.print(block.compose())
            if self._last_tool_call_block == block:
                self._last_tool_call_block = None
            self.refresh_soon()

    def flush_notifications(self) -> None:
        """将渲染的通知刷新到终端历史记录。"""
        self._live_notification_blocks.clear()
        while self._notification_blocks:
            console.print(self._notification_blocks.popleft().compose())
            self.refresh_soon()

    def append_content(self, part: ContentPart) -> None:
        """追加内容部分。

        Args:
            part: 内容部分对象。
        """
        match part:
            case ThinkPart(think=text) | TextPart(text=text):
                if not text:
                    return
                is_think = isinstance(part, ThinkPart)
                if self._current_content_block is None:
                    self._current_content_block = _ContentBlock(is_think)
                    self.refresh_soon()
                elif self._current_content_block.is_think != is_think:
                    self.flush_content()
                    self._current_content_block = _ContentBlock(is_think)
                    self.refresh_soon()
                self._current_content_block.append(text)
                self.refresh_soon()
            case _:
                # TODO: 支持更多内容部分类型
                pass

    def append_tool_call(self, tool_call: ToolCall) -> None:
        """追加工具调用。

        Args:
            tool_call: 工具调用对象。
        """
        self.flush_content()
        self._tool_call_blocks[tool_call.id] = _ToolCallBlock(tool_call)
        self._last_tool_call_block = self._tool_call_blocks[tool_call.id]
        self.refresh_soon()

    def append_tool_call_part(self, part: ToolCallPart) -> None:
        """追加工具调用参数片段。

        Args:
            part: 工具调用参数片段对象。
        """
        if not part.arguments_part:
            return
        if self._last_tool_call_block is None:
            return
        self._last_tool_call_block.append_args_part(part.arguments_part)
        self.refresh_soon()

    def append_tool_result(self, result: ToolResult) -> None:
        """追加工具执行结果。

        Args:
            result: 工具执行结果对象。
        """
        if block := self._tool_call_blocks.get(result.tool_call_id):
            block.finish(result.return_value)
            self.flush_finished_tool_calls()
            self.refresh_soon()

    def append_notification(self, notification: Notification) -> None:
        """追加通知消息。

        Args:
            notification: 通知对象。
        """
        block = _NotificationBlock(notification)
        self._notification_blocks.append(block)
        self._live_notification_blocks.append(block)
        self.refresh_soon()

    def request_approval(self, request: ApprovalRequest) -> None:
        """请求审批。

        Args:
            request: 审批请求对象。
        """
        self._approval_request_queue.append(request)

        if self._current_approval_request_panel is None:
            console.bell()
            self.show_next_approval_request()

    def _reconcile_approval_requests(self) -> None:
        """协调审批请求队列，清理已解决的请求。"""
        self._approval_request_queue = deque(
            request for request in self._approval_request_queue if not request.resolved
        )
        if (
            self._current_approval_request_panel is not None
            and self._current_approval_request_panel.request.resolved
        ):
            self._current_approval_request_panel = None
            self.show_next_approval_request()
        else:
            self.refresh_soon()

    def show_next_approval_request(self) -> None:
        """显示队列中的下一个审批请求。

        若无待处理请求，则清除当前审批面板。
        """
        if not self._approval_request_queue:
            if self._current_approval_request_panel is not None:
                self._current_approval_request_panel = None
                self.refresh_soon()
            return

        while self._approval_request_queue:
            request = self._approval_request_queue.popleft()
            if request.resolved:
                # 跳过已解决的请求
                continue
            self._current_approval_request_panel = ApprovalRequestPanel(request)
            self.refresh_soon()
            break
        else:
            # 队列中所有请求均已解决
            if self._current_approval_request_panel is not None:
                self._current_approval_request_panel = None
                self.refresh_soon()

    def display_plan(self, msg: PlanDisplay) -> None:
        """在聊天中以内联带边框面板的形式渲染计划内容。

        Args:
            msg: 计划显示消息对象。
        """
        self.flush_content()
        self.flush_finished_tool_calls()
        plan_body = Markdown(msg.content)
        subtitle = Text(msg.file_path, style="dim")
        panel = Panel(
            plan_body,
            title="[bold cyan]Plan[/bold cyan]",
            title_align="left",
            subtitle=subtitle,
            subtitle_align="left",
            border_style="cyan",
            padding=(1, 2),
        )
        console.print(panel)

    def request_question(self, request: QuestionRequest) -> None:
        """请求问答。

        Args:
            request: 问答请求对象。
        """
        self._question_request_queue.append(request)
        if self._current_question_panel is None:
            console.bell()
            self.show_next_question_request()

    def show_next_question_request(self) -> None:
        """显示队列中的下一个问答请求。"""
        if not self._question_request_queue:
            if self._current_question_panel is not None:
                self._current_question_panel = None
                self.refresh_soon()
                self._on_question_panel_state_changed()
            return

        while self._question_request_queue:
            request = self._question_request_queue.popleft()
            if request.resolved:
                continue
            self._current_question_panel = QuestionRequestPanel(request)
            self.refresh_soon()
            self._on_question_panel_state_changed()
            break
        else:
            # 队列中所有请求均已解决
            if self._current_question_panel is not None:
                self._current_question_panel = None
                self.refresh_soon()
                self._on_question_panel_state_changed()

    def handle_subagent_event(self, event: SubagentEvent) -> None:
        """处理子智能体事件。

        Args:
            event: 子智能体事件对象。
        """
        if event.parent_tool_call_id is None:
            return
        block = self._tool_call_blocks.get(event.parent_tool_call_id)
        if block is None:
            return
        if event.agent_id is not None and event.subagent_type is not None:
            block.set_subagent_metadata(event.agent_id, event.subagent_type)

        match event.event:
            case ToolCall() as tool_call:
                block.append_sub_tool_call(tool_call)
            case ToolCallPart() as tool_call_part:
                block.append_sub_tool_call_part(tool_call_part)
            case ToolResult() as tool_result:
                block.finish_sub_tool_call(tool_result)
                self.refresh_soon()
            case _:
                # 目前忽略其他事件
                # TODO: 可能需要处理多层嵌套子智能体
                pass


class _PromptLiveView(_LiveView):
    """带提示会话的实时视图类。

    扩展基础实时视图，支持交互式提示会话，包括用户输入处理、
    运行时提示渲染和模态问答面板集成。

    Attributes:
        modal_priority: 模态优先级。
        _prompt_session: 自定义提示会话。
        _steer: 用户输入注入回调。
        _pending_local_steers: 待处理的本地注入队列。
        _turn_ended: 当前回合是否已结束。
        _question_modal: 问答模态委托对象。
    """

    modal_priority = 0

    def __init__(
        self,
        initial_status: StatusUpdate,
        *,
        prompt_session: CustomPromptSession,
        steer: Callable[[str | list[ContentPart]], None],
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """初始化提示实时视图。

        Args:
            initial_status: 初始状态更新对象。
            prompt_session: 自定义提示会话。
            steer: 用户输入注入回调。
            cancel_event: 取消事件。
        """
        super().__init__(initial_status, cancel_event)
        self._prompt_session = prompt_session
        self._steer = steer
        self._pending_local_steers: deque[str | list[ContentPart]] = deque()
        self._turn_ended = False
        self._question_modal: QuestionPromptDelegate | None = None

    async def visualize_loop(self, wire: WireUISide):
        """可视化主循环（提示会话版本）。

        Args:
            wire: Wire 通信通道。
        """
        try:
            wire_task = asyncio.create_task(wire.receive())
            external_task = asyncio.create_task(self._external_messages.get())
            while True:
                try:
                    done, _ = await asyncio.wait(
                        [wire_task, external_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if wire_task in done:
                        msg = wire_task.result()
                        wire_task = asyncio.create_task(wire.receive())
                    else:
                        msg = external_task.result()
                        external_task = asyncio.create_task(self._external_messages.get())
                except QueueShutDown:
                    msg, external_task = await self._drain_external_message_after_wire_shutdown(
                        external_task
                    )
                    if msg is not None:
                        self.dispatch_wire_message(msg)
                        self._flush_prompt_refresh()
                        continue
                    self.cleanup(is_interrupt=False)
                    self._flush_prompt_refresh()
                    break

                if isinstance(msg, StepInterrupted):
                    self.cleanup(is_interrupt=True)
                    self._flush_prompt_refresh()
                    break

                if isinstance(msg, TurnEnd):
                    self._turn_ended = True
                    self._flush_prompt_refresh()
                    continue

                self.dispatch_wire_message(msg)
                self._flush_prompt_refresh()
        finally:
            self._external_messages.shutdown(immediate=True)
            for task in (locals().get("wire_task"), locals().get("external_task")):
                if task is None:
                    continue
                task.cancel()
                with suppress(asyncio.CancelledError, QueueShutDown):
                    await task
            self._pending_local_steers.clear()
            self._turn_ended = False
            if self._question_modal is not None:
                self._prompt_session.detach_modal(self._question_modal)
                self._question_modal = None
            self._prompt_session.invalidate()

    def handle_local_input(self, user_input: UserInput) -> None:
        """处理本地用户输入。

        Args:
            user_input: 用户输入对象。
        """
        if not user_input or self._turn_ended:
            return

        console.print(render_user_echo_text(user_input.command))
        self._pending_local_steers.append(list(user_input.content))
        self._steer(user_input.content)
        self._flush_prompt_refresh()

    def dispatch_wire_message(self, msg: WireMessage) -> None:
        """分发 Wire 消息，过滤本地注入的重复消息。

        Args:
            msg: Wire 消息对象。
        """
        if isinstance(msg, SteerInput) and self._pending_local_steers:
            pending = self._pending_local_steers[0]
            if pending == msg.user_input:
                self._pending_local_steers.popleft()
                return
        super().dispatch_wire_message(msg)

    def render_running_prompt_body(self, columns: int) -> ANSI:
        """渲染运行时提示的主体内容。

        Args:
            columns: 终端列数。

        Returns:
            ANSI 格式的渲染内容。
        """
        if (
            self._turn_ended
            and self._current_approval_request_panel is None
            and self._current_question_panel is None
        ):
            return ANSI("")
        renderable = self.compose(include_status=False)
        body = render_to_ansi(renderable, columns=columns).rstrip("\n")
        return ANSI(body if body else "")

    def running_prompt_placeholder(self) -> str | None:
        """获取运行时提示的占位符文本。

        Returns:
            占位符文本，若无则返回 None。
        """
        if self._current_approval_request_panel is not None:
            return "Use ↑/↓ or 1/2/3, then press Enter to respond to the approval request."
        return None

    def running_prompt_hides_input_buffer(self) -> bool:
        """检查运行时提示是否隐藏输入缓冲区。

        Returns:
            是否隐藏输入缓冲区。
        """
        return False

    def running_prompt_allows_text_input(self) -> bool:
        """检查运行时提示是否允许文本输入。

        Returns:
            是否允许文本输入。
        """
        if self._current_approval_request_panel is not None:
            return False
        if self._current_question_panel is not None:
            return False
        return not self._turn_ended

    def running_prompt_accepts_submission(self) -> bool:
        """检查运行时提示是否接受提交。

        Returns:
            是否接受提交。
        """
        if self._current_approval_request_panel is not None:
            return True
        if self._current_question_panel is not None:
            return True
        return not self._turn_ended

    def should_handle_running_prompt_key(self, key: str) -> bool:
        """检查是否应处理运行时提示的按键。

        Args:
            key: 按键标识。

        Returns:
            是否应处理该按键。
        """
        if key == "c-e":
            return self.has_expandable_panel()
        if self._current_approval_request_panel is not None:
            return key in {"up", "down", "enter", "1", "2", "3", "4"}
        if self._turn_ended:
            return False
        if key == "escape":
            return self._cancel_event is not None
        return False

    def handle_running_prompt_key(self, key: str, event: KeyPressEvent) -> None:
        """处理运行时提示的按键事件。

        Args:
            key: 按键标识。
            event: 按键事件对象。
        """
        if key == "c-e":
            event.app.create_background_task(self._show_panel_in_pager())
            return

        mapped = {
            "up": KeyEvent.UP,
            "down": KeyEvent.DOWN,
            "enter": KeyEvent.ENTER,
            "escape": KeyEvent.ESCAPE,
            "1": KeyEvent.NUM_1,
            "2": KeyEvent.NUM_2,
            "3": KeyEvent.NUM_3,
            "4": KeyEvent.NUM_4,
        }.get(key)
        if mapped is None:
            return
        if self._current_approval_request_panel is not None:
            self._clear_buffer(event.current_buffer)
        self.dispatch_keyboard_event(mapped)
        self._flush_prompt_refresh()

    async def _show_panel_in_pager(self) -> None:
        """在 pager 中显示面板内容。"""
        await run_in_terminal(self._show_expandable_panel_content)
        self._prompt_session.invalidate()

    @staticmethod
    def _clear_buffer(buffer: Buffer) -> None:
        """清除缓冲区内容。

        Args:
            buffer: 缓冲区对象。
        """
        if buffer.text:
            buffer.document = Document(text="", cursor_position=0)

    def _flush_prompt_refresh(self) -> None:
        """刷新提示会话显示。"""
        if self._need_recompose:
            self._prompt_session.invalidate()
            self._need_recompose = False

    def cleanup(self, is_interrupt: bool) -> None:
        """清理实时视图。

        Args:
            is_interrupt: 是否为中断导致的清理。
        """
        super().cleanup(is_interrupt)

    def _on_question_panel_state_changed(self) -> None:
        """问答面板状态变化的钩子方法。"""
        panel = self._current_question_panel
        if panel is None:
            if self._question_modal is not None:
                self._prompt_session.detach_modal(self._question_modal)
                self._question_modal = None
            return
        if self._question_modal is None:
            self._question_modal = QuestionPromptDelegate(
                panel,
                on_advance=self._advance_question,
                on_invalidate=self._flush_prompt_refresh,
                buffer_text_provider=lambda: self._prompt_session._session.default_buffer.text,  # pyright: ignore[reportPrivateUsage]
                text_expander=self._prompt_session._get_placeholder_manager().serialize_for_history,  # pyright: ignore[reportPrivateUsage]
            )
            self._prompt_session.attach_modal(self._question_modal)
        else:
            self._question_modal.set_panel(panel)
        self._prompt_session.invalidate()

    def _advance_question(self) -> QuestionRequestPanel | None:
        """推进到队列中的下一个问答，返回新面板或 None。

        Returns:
            新的问答面板，若无则返回 None。
        """
        self.show_next_question_request()
        return self._current_question_panel
