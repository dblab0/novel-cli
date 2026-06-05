"""审批请求面板模块。

本模块提供审批请求的交互式面板界面，用于展示需要用户确认的操作请求，
包括代码修改差异、命令执行等内容的预览和审批选项。

主要组件：
- ApprovalContentBlock: 审批内容块数据类
- ApprovalRequestPanel: 审批请求面板
- ApprovalPromptDelegate: 审批提示委托处理器
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from prompt_toolkit.application.run_in_terminal import run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyPressEvent
from rich.console import Group, RenderableType
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from novel_cli.ui.shell.console import console, render_to_ansi
from novel_cli.ui.shell.keyboard import KeyEvent
from novel_cli.utils.rich.diff_render import (
    collect_diff_hunks,
    render_diff_panel,
    render_diff_preview,
    render_diff_summary_panel,
    render_diff_summary_preview,
)
from novel_cli.utils.rich.syntax import NovelSyntax
from novel_cli.wire.types import (
    ApprovalRequest,
    ApprovalResponse,
    BriefDisplayBlock,
    DiffDisplayBlock,
    ShellDisplayBlock,
)

# 审批请求预览显示的最大行数
MAX_PREVIEW_LINES = 4


class ApprovalContentBlock(NamedTuple):
    """预渲染的审批内容块数据类。

    用于存储非差异类型的内容块及其元数据。

    Attributes:
        text: 内容文本。
        lines: 文本行数。
        style: Rich 样式字符串。
        lexer: 语法高亮器名称（用于代码块）。
    """

    text: str
    lines: int
    style: str = ""
    lexer: str = ""


class ApprovalRequestPanel:
    """审批请求面板。

    管理审批请求的渲染和用户交互，包括选项选择和反馈输入。

    Attributes:
        request: 审批请求对象。
        options: 审批选项列表，每项包含显示文本和响应类型。
        selected_index: 当前选中的选项索引。
        FEEDBACK_OPTION_INDEX: 反反馈输入选项的索引位置。
        _preview_renderables: 预览渲染对象列表。
        _has_diff: 是否包含差异内容。
        _non_diff_truncated: 非差异内容是否被截断。
        _content_blocks: 非差异内容块列表（用于完整显示）。
        has_expandable_content: 是否有可展开的内容。
    """

    FEEDBACK_OPTION_INDEX = 3

    def __init__(self, request: ApprovalRequest):
        """初始化审批请求面板。

        Args:
            request: 审批请求对象。
        """
        self.request = request
        self.options: list[tuple[str, ApprovalResponse.Kind]] = [
            ("Approve once", "approve"),
            ("Approve for this session", "approve_for_session"),
            ("Reject", "reject"),
            ("Reject, tell the model what to do instead", "reject"),
        ]
        self.selected_index = 0

        # 预渲染预览内容
        # 所有块（差异和非差异）按原始显示顺序渲染到单一列表中，以保留交错顺序
        self._preview_renderables: list[RenderableType] = []
        self._has_diff = False
        self._non_diff_truncated = False
        # 非差异块的内容块列表（用于 render_full 回退）
        self._content_blocks: list[ApprovalContentBlock] = []

        # 非差异块的行数预算
        non_diff_budget = MAX_PREVIEW_LINES

        # 处理描述文本（仅当无显示块时）
        if request.description and not request.display:
            text = request.description.rstrip("\n")
            line_count = text.count("\n") + 1
            self._content_blocks.append(ApprovalContentBlock(text=text, lines=line_count))
            preview_text = text
            if line_count > non_diff_budget:
                preview_text = "\n".join(text.split("\n")[:non_diff_budget])
                self._non_diff_truncated = True
            self._preview_renderables.append(Text(preview_text))
            non_diff_budget -= min(line_count, non_diff_budget)

        # 处理显示块——合并相同文件的连续 DiffDisplayBlock
        display = request.display
        idx = 0
        while idx < len(display):
            block = display[idx]
            if isinstance(block, DiffDisplayBlock):
                path = block.path
                diff_blocks: list[DiffDisplayBlock] = []
                while idx < len(display):
                    b = display[idx]
                    if not isinstance(b, DiffDisplayBlock) or b.path != path:
                        break
                    diff_blocks.append(b)
                    idx += 1
                if any(b.is_summary for b in diff_blocks):
                    self._has_diff = True
                    self._preview_renderables.extend(render_diff_summary_preview(path, diff_blocks))
                else:
                    hunks, added, removed = collect_diff_hunks(diff_blocks)
                    if hunks:
                        self._has_diff = True
                        renderables, _remaining = render_diff_preview(
                            path,
                            hunks,
                            added,
                            removed,
                        )
                        self._preview_renderables.extend(renderables)
            elif isinstance(block, ShellDisplayBlock):
                text = block.command.rstrip("\n")
                line_count = text.count("\n") + 1
                self._content_blocks.append(
                    ApprovalContentBlock(text=text, lines=line_count, lexer=block.language)
                )
                if non_diff_budget > 0:
                    truncated = text
                    if line_count > non_diff_budget:
                        truncated = "\n".join(text.split("\n")[:non_diff_budget])
                        self._non_diff_truncated = True
                    self._preview_renderables.append(NovelSyntax(truncated, block.language))
                    non_diff_budget -= min(line_count, non_diff_budget)
                else:
                    self._non_diff_truncated = True
                idx += 1
            elif isinstance(block, BriefDisplayBlock) and block.text:
                text = block.text.rstrip("\n")
                line_count = text.count("\n") + 1
                self._content_blocks.append(
                    ApprovalContentBlock(text=text, lines=line_count, style="grey50")
                )
                if non_diff_budget > 0:
                    truncated = text
                    if line_count > non_diff_budget:
                        truncated = "\n".join(text.split("\n")[:non_diff_budget])
                        self._non_diff_truncated = True
                    self._preview_renderables.append(Text(truncated, style="grey50"))
                    non_diff_budget -= min(line_count, non_diff_budget)
                else:
                    self._non_diff_truncated = True
                idx += 1
            else:
                idx += 1

        # P1: 差异 pager 总有未在预览中显示的上下文行
        # P2: 非差异块可能被截断
        self.has_expandable_content = self._has_diff or self._non_diff_truncated

    def render(self, *, feedback_text: str | None = None) -> RenderableType:
        """渲染审批菜单为带边框的面板。

        Args:
            feedback_text: 可选的反馈输入文本，用于显示在反馈输入框中。

        Returns:
            Rich 可渲染对象，包含完整的审批面板。
        """
        content_lines: list[RenderableType] = [
            Text.from_markup(
                "[yellow]"
                f"{escape(self.request.sender)} is requesting approval to "
                f"{escape(self.request.action)}:[/yellow]"
            )
        ]
        content_lines.extend(self._render_source_metadata_lines())
        content_lines.append(Text(""))

        # 渲染预览内容（差异和非差异按原始显示顺序）
        content_lines.extend(self._preview_renderables)

        if self.has_expandable_content and self._non_diff_truncated:
            content_lines.append(Text("... (truncated, ctrl-e to expand)", style="dim italic"))

        lines: list[RenderableType] = []
        if content_lines:
            lines.append(Padding(Group(*content_lines), (0, 0, 0, 1)))

        # 是否显示内联反馈输入
        show_inline_feedback = feedback_text is not None and self.is_feedback_selected

        # 添加带数字键标签的菜单选项
        if lines:
            lines.append(Text(""))
        for i, (option_text, _) in enumerate(self.options):
            num = i + 1
            is_feedback_option = i == self.FEEDBACK_OPTION_INDEX
            if i == self.selected_index:
                if is_feedback_option and show_inline_feedback:
                    input_display = escape(feedback_text) if feedback_text else ""
                    lines.append(
                        Text.from_markup(
                            f"[cyan]\u2192 \\[{num}] Reject: {input_display}\u2588[/cyan]"
                        )
                    )
                else:
                    lines.append(Text(f"\u2192 [{num}] {option_text}", style="cyan"))
            else:
                lines.append(Text(f"  [{num}] {option_text}", style="grey50"))

        # 键盘快捷键提示
        lines.append(Text(""))
        if show_inline_feedback:
            hint = "  Type your feedback, then press Enter to submit."
        else:
            hint = "  \u25b2/\u25bc select  1/2/3/4 choose  \u21b5 confirm"
            if self.has_expandable_content:
                hint += "  ctrl-e expand"
        lines.append(Text(hint, style="dim"))

        return Panel(
            Group(*lines),
            border_style="bold yellow",
            title="[bold yellow]\u26a0 ACTION REQUIRED[/bold yellow]",
            title_align="left",
            padding=(0, 1),
        )

    def _render_block(
        self, block: ApprovalContentBlock, max_lines: int | None = None
    ) -> RenderableType:
        """渲染单个内容块。

        Args:
            block: 内容块对象。
            max_lines: 可选的最大行数限制，超出时截断。

        Returns:
            Rich 可渲染对象。
        """
        text = block.text
        if max_lines is not None and block.lines > max_lines:
            text = "\n".join(text.split("\n")[:max_lines])

        if block.lexer:
            return NovelSyntax(text, block.lexer)
        return Text(text, style=block.style)

    def render_full(self) -> list[RenderableType]:
        """渲染完整内容用于 pager（无截断）。

        Returns:
            内容块渲染对象列表。
        """
        return [self._render_block(block) for block in self._content_blocks]

    def _render_source_metadata_lines(self) -> list[RenderableType]:
        """渲染来源元数据行。

        Returns:
            包含子智能体和任务描述的渲染对象列表。
        """
        lines: list[RenderableType] = []
        if self.request.subagent_type is not None or self.request.agent_id is not None:
            if self.request.subagent_type is not None and self.request.agent_id is not None:
                subagent_text = f"{self.request.subagent_type} ({self.request.agent_id})"
            elif self.request.subagent_type is not None:
                subagent_text = self.request.subagent_type
            else:
                assert self.request.agent_id is not None
                subagent_text = self.request.agent_id
            lines.append(Text(f"Subagent: {subagent_text}", style="grey50"))
        if self.request.source_description:
            lines.append(Text(f"Task: {self.request.source_description}", style="grey50"))
        return lines

    def move_up(self):
        """向上移动选项选择。"""
        self.selected_index = (self.selected_index - 1) % len(self.options)

    def move_down(self):
        """向下移动选项选择。"""
        self.selected_index = (self.selected_index + 1) % len(self.options)

    @property
    def is_feedback_selected(self) -> bool:
        """判断是否选中反馈输入选项。

        Returns:
            当前选中反馈选项返回 True。
        """
        return self.selected_index == self.FEEDBACK_OPTION_INDEX

    def get_selected_response(self) -> ApprovalResponse.Kind:
        """获取当前选中选项对应的响应类型。

        Returns:
            响应类型字符串，如 ``approve`` 或 ``reject``。
        """
        return self.options[self.selected_index][1]


def show_approval_in_pager(panel: ApprovalRequestPanel) -> None:
    """在 pager 中显示完整的审批请求内容。

    Args:
        panel: 审批请求面板对象。
    """
    with console.screen(), console.pager(styles=True):
        console.print(
            Text.from_markup(
                "[yellow]⚠ "
                f"{escape(panel.request.sender)} is requesting approval to "
                f"{escape(panel.request.action)}:[/yellow]"
            )
        )
        console.print()

        # 使用统一差异渲染器渲染显示块
        display = panel.request.display
        rendered_any = False
        idx = 0
        while idx < len(display):
            block = display[idx]
            if isinstance(block, DiffDisplayBlock):
                path = block.path
                diff_blocks: list[DiffDisplayBlock] = []
                while idx < len(display):
                    b = display[idx]
                    if not isinstance(b, DiffDisplayBlock) or b.path != path:
                        break
                    diff_blocks.append(b)
                    idx += 1
                if any(b.is_summary for b in diff_blocks):
                    console.print(render_diff_summary_panel(path, diff_blocks))
                    rendered_any = True
                else:
                    hunks, added, removed = collect_diff_hunks(diff_blocks)
                    if hunks:
                        console.print(render_diff_panel(path, hunks, added, removed))
                        rendered_any = True
            elif isinstance(block, ShellDisplayBlock):
                console.print(NovelSyntax(block.command.rstrip("\n"), block.language))
                rendered_any = True
                idx += 1
            elif isinstance(block, BriefDisplayBlock) and block.text:
                console.print(Text(block.text.rstrip("\n"), style="grey50"))
                rendered_any = True
                idx += 1
            else:
                idx += 1

        # 回退：如果未渲染任何内容（如反序列化后类型不匹配），使用旧版预渲染内容块
        if not rendered_any:
            for renderable in panel.render_full():
                console.print(renderable)


class ApprovalPromptDelegate:
    """审批提示委托处理器。

    处理审批请求在提示界面中的交互逻辑，包括键盘事件处理和响应提交。

    Attributes:
        modal_priority: 模态优先级，用于确定提示界面层级。
        _KEY_MAP: 按键名称到 KeyEvent 的映射字典。
        _panel: 审批请求面板实例。
        _on_response: 响应回调函数。
        _buffer_text_provider: 缓冲区文本提供函数。
        _text_expander: 文本展开函数（用于处理占位符）。
        _feedback_draft: 反反馈草稿文本。
    """

    modal_priority = 20
    _KEY_MAP: dict[str, KeyEvent] = {
        "up": KeyEvent.UP,
        "down": KeyEvent.DOWN,
        "enter": KeyEvent.ENTER,
        "1": KeyEvent.NUM_1,
        "2": KeyEvent.NUM_2,
        "3": KeyEvent.NUM_3,
        "4": KeyEvent.NUM_4,
        "escape": KeyEvent.ESCAPE,
        "c-c": KeyEvent.ESCAPE,
        "c-d": KeyEvent.ESCAPE,
    }

    def __init__(
        self,
        request: ApprovalRequest,
        *,
        on_response: Callable[[ApprovalRequest, ApprovalResponse.Kind, str], None],
        buffer_text_provider: Callable[[], str] | None = None,
        text_expander: Callable[[str], str] | None = None,
    ) -> None:
        """初始化审批提示委托处理器。

        Args:
            request: 审批请求对象。
            on_response: 响应回调函数，接收请求、响应类型和反馈文本。
            buffer_text_provider: 可选的缓冲区文本提供函数，用于获取输入框内容。
            text_expander: 可选的文本展开函数，用于处理占位符。
        """
        self._panel = ApprovalRequestPanel(request)
        self._on_response = on_response
        self._buffer_text_provider = buffer_text_provider
        self._text_expander = text_expander
        self._feedback_draft: str = ""

    @property
    def request(self) -> ApprovalRequest:
        """获取当前审批请求。

        Returns:
            审批请求对象。
        """
        return self._panel.request

    def set_request(self, request: ApprovalRequest) -> None:
        """设置新的审批请求。

        Args:
            request: 新的审批请求对象。
        """
        self._panel = ApprovalRequestPanel(request)
        self._feedback_draft = ""

    def _is_inline_feedback_active(self) -> bool:
        """判断内联反馈输入是否激活。

        Returns:
            反馈选项选中且有缓冲区文本提供器返回 True。
        """
        return self._panel.is_feedback_selected and self._buffer_text_provider is not None

    def render_running_prompt_body(self, columns: int) -> ANSI:
        """渲染运行提示的主体内容。

        Args:
            columns: 终端列数。

        Returns:
            ANSI 格式化文本对象。
        """
        feedback_text: str | None = None
        if self._is_inline_feedback_active():
            feedback_text = self._buffer_text_provider() if self._buffer_text_provider else ""
        body = render_to_ansi(
            self._panel.render(feedback_text=feedback_text),
            columns=columns,
        ).rstrip("\n")
        return ANSI(body)

    def running_prompt_placeholder(self) -> str | None:
        """获取运行提示的占位符文本。

        Returns:
            占位符文本，无占位符返回 None。
        """
        return None

    def running_prompt_allows_text_input(self) -> bool:
        """判断运行提示是否允许文本输入。

        Returns:
            内联反馈激活时返回 True。
        """
        return self._is_inline_feedback_active()

    def running_prompt_hides_input_buffer(self) -> bool:
        """判断运行提示是否隐藏输入缓冲区。

        Returns:
            始终返回 True，输入由面板内部控制。
        """
        return True

    def running_prompt_accepts_submission(self) -> bool:
        """判断运行提示是否接受提交。

        Returns:
            始终返回 False，提交由键盘事件处理。
        """
        return False

    def should_handle_running_prompt_key(self, key: str) -> bool:
        """判断是否应该处理指定按键。

        Args:
            key: 按键名称。

        Returns:
            应处理该按键返回 True。
        """
        if key == "c-e":
            return self._panel.has_expandable_content
        if self._is_inline_feedback_active():
            return key in {"enter", "escape", "c-c", "c-d", "up", "down"}
        return key in {
            "up",
            "down",
            "enter",
            "1",
            "2",
            "3",
            "4",
            "escape",
            "c-c",
            "c-d",
            "c-e",
        }

    def handle_running_prompt_key(self, key: str, event: KeyPressEvent) -> None:
        """处理运行提示的按键事件。

        Args:
            key: 按键名称。
            event: 按键事件对象。
        """
        if key == "c-e":
            event.app.create_background_task(self._show_panel_in_pager())
            return

        # 内联反馈模式：用户在"拒绝 + 反馈"字段中输入
        if self._is_inline_feedback_active():
            mapped = self._KEY_MAP.get(key)
            if key == "enter" or mapped == KeyEvent.ENTER:
                text = event.current_buffer.text.strip()
                if text:
                    if self._text_expander is not None:
                        text = self._text_expander(text)
                    self._clear_buffer(event.current_buffer)
                    self._feedback_draft = ""
                    self._panel.request.resolve("reject")
                    self._on_response(self._panel.request, "reject", text)
                # 空输入按回车：不做任何操作（继续编辑）
                return
            if mapped == KeyEvent.ESCAPE:
                self._clear_buffer(event.current_buffer)
                self._feedback_draft = ""
                self._panel.request.resolve("reject")
                self._on_response(self._panel.request, "reject", "")
                return
            if mapped in {KeyEvent.UP, KeyEvent.DOWN}:
                self._feedback_draft = event.current_buffer.text
                self._clear_buffer(event.current_buffer)
                if mapped == KeyEvent.UP:
                    self._panel.move_up()
                else:
                    self._panel.move_down()
                return
            return

        mapped = self._KEY_MAP.get(key)
        if mapped is None:
            return
        match mapped:
            case KeyEvent.UP:
                self._panel.move_up()
                self._maybe_restore_feedback_draft(event.current_buffer)
            case KeyEvent.DOWN:
                self._panel.move_down()
                self._maybe_restore_feedback_draft(event.current_buffer)
            case KeyEvent.ENTER:
                self._submit_current_request(event.current_buffer)
            case KeyEvent.ESCAPE:
                self._panel.request.resolve("reject")
                self._on_response(self._panel.request, "reject", "")
            case KeyEvent.NUM_1 | KeyEvent.NUM_2 | KeyEvent.NUM_3 | KeyEvent.NUM_4:
                num_map = {
                    KeyEvent.NUM_1: 0,
                    KeyEvent.NUM_2: 1,
                    KeyEvent.NUM_3: 2,
                    KeyEvent.NUM_4: 3,
                }
                idx = num_map[mapped]
                if idx < len(self._panel.options):
                    self._panel.selected_index = idx
                    if not self._is_inline_feedback_active():
                        self._submit_current_request(event.current_buffer)
            case _:
                pass

    async def _show_panel_in_pager(self) -> None:
        """在 pager 中显示审批面板完整内容。"""
        await run_in_terminal(lambda: show_approval_in_pager(self._panel))

    def _maybe_restore_feedback_draft(self, buffer: Buffer) -> None:
        """如果反馈选项激活且有草稿，则恢复草稿到缓冲区。

        Args:
            buffer: 输入缓冲区对象。
        """
        if self._is_inline_feedback_active() and self._feedback_draft:
            buffer.set_document(
                Document(text=self._feedback_draft, cursor_position=len(self._feedback_draft)),
                bypass_readonly=True,
            )

    @staticmethod
    def _clear_buffer(buffer: Buffer) -> None:
        """清空输入缓冲区。

        Args:
            buffer: 输入缓冲区对象。
        """
        if buffer.text:
            buffer.set_document(Document(text="", cursor_position=0), bypass_readonly=True)

    def _submit_current_request(self, buffer: Buffer) -> None:
        """提交当前审批请求。

        Args:
            buffer: 输入缓冲区对象。
        """
        self._clear_buffer(buffer)
        self._feedback_draft = ""
        response = self._panel.get_selected_response()
        self._panel.request.resolve(response)
        self._on_response(self._panel.request, response, "")
