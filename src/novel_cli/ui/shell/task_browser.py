"""后台任务浏览器模块。

本模块提供后台任务的交互式浏览界面，用户可以查看任务列表、
任务详情和输出内容，以及执行停止任务等操作。

主要组件：
- TaskBrowserModel: 任务浏览器数据模型
- TaskBrowserApp: 任务浏览器 TUI 应用
"""

import time
from dataclasses import dataclass, field
from typing import Literal

from prompt_toolkit.application import Application
from prompt_toolkit.application.run_in_terminal import run_in_terminal
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Box, Frame, RadioList
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from novel_cli.background import TaskView, is_terminal_status
from novel_cli.soul.novelsoul import NovelSoul
from novel_cli.ui.shell.console import console
from novel_cli.utils.datetime import format_duration, format_relative_time

# 任务浏览器过滤器类型
TaskBrowserFilter = Literal["all", "active"]

# 空任务列表占位符 ID
_EMPTY_TASK_ID = "__empty__"
# 预览区域最大行数
_PREVIEW_MAX_LINES = 6
# 预览区域最大字节数
_PREVIEW_MAX_BYTES = 4_000
# 完整输出最大字节数
_FULL_OUTPUT_MAX_BYTES = 200_000
# 完整输出最大行数
_FULL_OUTPUT_MAX_LINES = 4_000
# 自动刷新间隔（秒）
_AUTO_REFRESH_SECONDS = 1.0
# 闪现消息持续时间（秒）
_FLASH_MESSAGE_SECONDS = 3.0


def format_task_choice(view: TaskView, *, now: float | None = None) -> str:
    """格式化任务选项的显示文本。

    Args:
        view: 任务视图对象。
        now: 当前时间戳，默认使用实际当前时间。

    Returns:
        格式化的任务选择文本，包含状态、描述、ID、类型和时间信息。
    """
    description = view.spec.description.strip() or "(no description)"
    return " · ".join(
        [
            f"[{view.runtime.status}]",
            description,
            view.spec.id,
            view.spec.kind,
            _task_timing_label(view, now=now) or "updated just now",
        ]
    )


@dataclass(slots=True)
class TaskBrowserModel:
    """任务浏览器数据模型。

    Attributes:
        soul: NovelSoul 实例，用于访问运行时环境。
        filter_mode: 过滤模式，``all`` 显示所有任务，``active`` 仅显示活跃任务。
        message: 闪现消息文本。
        message_expires_at: 消息过期时间戳。
        pending_stop_task_id: 待确认停止的任务 ID。
        all_views: 所有任务视图列表。
        visible_views: 当前可见的任务视图列表。
    """

    soul: NovelSoul
    filter_mode: TaskBrowserFilter = "all"
    message: str = ""
    message_expires_at: float | None = None
    pending_stop_task_id: str | None = None
    all_views: list[TaskView] = field(default_factory=lambda: [])
    visible_views: list[TaskView] = field(default_factory=lambda: [])

    @property
    def manager(self):
        """获取后台任务管理器。

        Returns:
            后台任务管理器实例。
        """
        return self.soul.runtime.background_tasks

    @property
    def config(self):
        """获取后台任务配置。

        Returns:
            后台任务配置对象。
        """
        return self.soul.runtime.config.background

    def refresh(self, selected_task_id: str | None = None) -> tuple[list[tuple[str, str]], str]:
        """刷新任务列表数据。

        Args:
            selected_task_id: 当前选中的任务 ID。

        Returns:
            任务选项列表和当前选中 ID 的元组。
        """
        self.manager.reconcile()
        self.all_views = self.manager.list_tasks(limit=None)
        self.all_views.sort(key=_task_sort_key)

        if self.filter_mode == "active":
            self.visible_views = [
                view for view in self.all_views if not is_terminal_status(view.runtime.status)
            ]
        else:
            self.visible_views = list(self.all_views)

        if not self.visible_views:
            label = (
                "No active background tasks."
                if self.filter_mode == "active"
                else "No background tasks in this session."
            )
            self.pending_stop_task_id = None
            return [(_EMPTY_TASK_ID, label)], _EMPTY_TASK_ID

        values = [(view.spec.id, format_task_choice(view)) for view in self.visible_views]
        valid_ids = {task_id for task_id, _label in values}
        selected = selected_task_id if selected_task_id in valid_ids else values[0][0]

        if self.pending_stop_task_id not in valid_ids:
            self.pending_stop_task_id = None
        return values, selected

    def view_for(self, task_id: str | None) -> TaskView | None:
        """根据任务 ID 获取任务视图。

        Args:
            task_id: 任务 ID。

        Returns:
            任务视图对象，不存在返回 None。
        """
        if not task_id or task_id == _EMPTY_TASK_ID:
            return None
        for view in self.visible_views:
            if view.spec.id == task_id:
                return view
        return self.manager.get_task(task_id)

    def set_message(self, text: str, *, duration_s: float = _FLASH_MESSAGE_SECONDS) -> None:
        """设置闪现消息。

        Args:
            text: 消息文本。
            duration_s: 消息持续时间（秒）。
        """
        self.message = text
        self.message_expires_at = time.time() + duration_s

    def current_message(self) -> str | None:
        """获取当前有效消息。

        Returns:
            当前消息文本，已过期或无消息返回 None。
        """
        if not self.message:
            return None
        if self.message_expires_at is None:
            return self.message
        if time.time() > self.message_expires_at:
            self.message = ""
            self.message_expires_at = None
            return None
        return self.message

    def summary_fragments(self) -> StyleAndTextTuples:
        """生成标题栏摘要内容。

        Returns:
            格式化文本片段列表，包含任务统计信息。
        """
        counts = {
            "running": 0,
            "starting": 0,
            "failed": 0,
            "completed": 0,
            "killed": 0,
            "lost": 0,
        }
        for view in self.all_views:
            counts[view.runtime.status] = counts.get(view.runtime.status, 0) + 1

        scope = "ALL" if self.filter_mode == "all" else "ACTIVE"
        return [
            ("class:header.title", " TASK BROWSER "),
            ("class:header.meta", f" filter={scope} "),
            ("class:status.running", f" {counts['running']} running "),
            ("class:status.info", f" {counts['starting']} starting "),
            ("class:status.error", f" {counts['failed']} failed "),
            ("class:status.success", f" {counts['completed']} completed "),
            ("class:status.warning", f" {counts['killed'] + counts['lost']} interrupted "),
            ("class:header.meta", f" {len(self.all_views)} total "),
        ]

    def detail_text(self, task_id: str | None) -> str:
        """生成任务详情文本。

        Args:
            task_id: 任务 ID。

        Returns:
            任务详情文本，包含 ID、状态、描述等信息。
        """
        view = self.view_for(task_id)
        if view is None:
            return "Select a task from the list."

        terminal_reason = "timed_out" if view.runtime.timed_out else view.runtime.status
        lines = [
            f"Task ID: {view.spec.id}",
            f"Status: {view.runtime.status}",
            f"Description: {view.spec.description}",
            f"Kind: {view.spec.kind}",
        ]
        timing = _task_timing_label(view)
        if timing:
            lines.append(f"Time: {timing}")
        if view.spec.cwd:
            lines.append(f"Cwd: {view.spec.cwd}")
        if view.spec.command:
            lines.append(f"Command: {view.spec.command}")
        if view.runtime.exit_code is not None:
            lines.append(f"Exit code: {view.runtime.exit_code}")
        lines.append(f"Terminal reason: {terminal_reason}")
        if view.runtime.failure_reason:
            lines.append(f"Reason: {view.runtime.failure_reason}")
        return "\n".join(lines)

    def preview_text(self, task_id: str | None) -> str:
        """获取任务输出预览文本。

        Args:
            task_id: 任务 ID。

        Returns:
            输出预览文本，截取最近几行内容。
        """
        view = self.view_for(task_id)
        if view is None:
            return "No output to preview."

        preview = self.manager.tail_output(
            view.spec.id,
            max_bytes=_PREVIEW_MAX_BYTES,
            max_lines=_PREVIEW_MAX_LINES,
        )
        if not preview:
            return "[no output available]"
        return preview

    def full_output(self, task_id: str | None) -> str:
        """获取任务完整输出文本。

        Args:
            task_id: 任务 ID。

        Returns:
            完整输出文本，可能截取部分内容以避免过大。
        """
        view = self.view_for(task_id)
        if view is None:
            return "[no output available]"

        path = self.manager.resolve_output_path(view.spec.id)
        total_size = path.stat().st_size if path.exists() else 0
        output = self.manager.tail_output(
            view.spec.id,
            max_bytes=max(self.config.read_max_bytes * 10, _FULL_OUTPUT_MAX_BYTES),
            max_lines=_FULL_OUTPUT_MAX_LINES,
        )
        max_bytes = max(self.config.read_max_bytes * 10, _FULL_OUTPUT_MAX_BYTES)
        if total_size > max_bytes:
            return (
                f"[showing last {max_bytes} bytes of {total_size} bytes]\n\n"
                f"{output or '[no output available]'}"
            )
        return output or "[no output available]"

    def footer_fragments(self, task_id: str | None) -> StyleAndTextTuples:
        """生成底部快捷键提示内容。

        Args:
            task_id: 当前选中任务 ID。

        Returns:
            格式化文本片段列表，包含快捷键提示和状态消息。
        """
        if self.pending_stop_task_id is not None:
            label = self.pending_stop_task_id
            return [
                ("class:footer.warning", f" Confirm stop {label}? "),
                ("class:footer.key", "Y"),
                ("class:footer.text", " confirm  "),
                ("class:footer.key", "N"),
                ("class:footer.text", " cancel "),
            ]

        fragments: StyleAndTextTuples = [
            ("class:footer.key", " Enter "),
            ("class:footer.text", "output  "),
            ("class:footer.key", "S"),
            ("class:footer.text", " stop  "),
            ("class:footer.key", "R"),
            ("class:footer.text", " refresh  "),
            ("class:footer.key", "Tab"),
            ("class:footer.text", " filter  "),
            ("class:footer.key", "Q"),
            ("class:footer.text", " exit "),
            ("class:footer.meta", f" auto-refresh {_AUTO_REFRESH_SECONDS:.0f}s "),
        ]
        if message := self.current_message():
            fragments.extend(
                [
                    ("class:footer.meta", " | "),
                    ("class:footer.flash", f" {message} "),
                ]
            )
        return fragments


class TaskBrowserApp:
    """任务浏览器 TUI 应用。

    提供交互式界面用于浏览和管理后台任务。

    Attributes:
        _model: 任务浏览器数据模型。
        _task_list: 任务列表单选控件。
        _app: prompt_toolkit 应用实例。
    """

    def __init__(self, soul: NovelSoul):
        """初始化任务浏览器应用。

        Args:
            soul: NovelSoul 实例，用于访问运行时环境。
        """
        self._model = TaskBrowserModel(soul)
        task_values, selected = self._model.refresh()
        self._task_list = RadioList(
            values=task_values,
            default=selected,
            show_numbers=False,
            select_on_focus=True,
            open_character="",
            select_character=">",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
            container_style="class:task-list",
            checked_style="class:task-list.checked",
        )
        self._app = self._build_app()

    async def run(self) -> None:
        """运行任务浏览器应用。"""
        await self._app.run_async()

    @property
    def _selected_task_id(self) -> str | None:
        """获取当前选中的任务 ID。

        Returns:
            任务 ID，空列表时返回 None。
        """
        current = self._task_list.current_value
        if current == _EMPTY_TASK_ID:
            return None
        return current

    def _open_output(self, app: Application[object], task_id: str) -> None:
        """在终端 pager 中打开任务输出。

        Args:
            app: 应用实例。
            task_id: 任务 ID。
        """
        app.create_background_task(self._show_output_in_terminal(task_id))

    async def _show_output_in_terminal(self, task_id: str) -> None:
        """在终端 pager 中显示任务输出。

        Args:
            task_id: 任务 ID。
        """
        def render() -> None:
            view = self._model.view_for(task_id)
            if view is None:
                console.print(f"[yellow]Task not found: {task_id}[/yellow]")
                return
            with console.pager(styles=True):
                console.print(_build_full_output_renderable(view, self._model.full_output(task_id)))

        await run_in_terminal(render)

    def _toggle_filter(self) -> None:
        """切换过滤器模式。"""
        self._model.filter_mode = "active" if self._model.filter_mode == "all" else "all"
        self._model.set_message(
            "Showing active tasks only."
            if self._model.filter_mode == "active"
            else "Showing all tasks."
        )
        self._sync_views()

    def _refresh_views(self) -> None:
        """刷新任务列表视图。"""
        self._model.set_message("Refreshed.")
        self._sync_views()

    def _request_stop_for_selected_task(self) -> None:
        """请求停止当前选中的任务。"""
        view = self._model.view_for(self._selected_task_id)
        if view is None:
            self._model.set_message("No task selected.")
        elif is_terminal_status(view.runtime.status):
            self._model.set_message(f"Task {view.spec.id} is already {view.runtime.status}.")
        else:
            self._model.pending_stop_task_id = view.spec.id
            self._model.message = ""
            self._model.message_expires_at = None

    def _confirm_stop_request(self) -> None:
        """确认停止任务请求。"""
        task_id = self._model.pending_stop_task_id
        self._model.pending_stop_task_id = None
        if task_id is None:
            return
        view = self._model.view_for(task_id)
        if view is None:
            self._model.set_message(f"Task not found: {task_id}")
        elif is_terminal_status(view.runtime.status):
            self._model.set_message(f"Task {task_id} is already {view.runtime.status}.")
        else:
            self._model.manager.kill(task_id)
            self._model.set_message(f"Stop requested for task {task_id}.")
        self._sync_views()

    def _cancel_stop_request(self) -> None:
        """取消停止任务请求。"""
        self._model.pending_stop_task_id = None
        self._model.set_message("Stop cancelled.")

    def _build_app(self) -> Application[None]:
        """构建 prompt_toolkit 应用实例。

        Returns:
            配置完成的 Application 实例。
        """
        kb = KeyBindings()

        @Condition
        def stop_pending() -> bool:
            """判断是否有待确认的停止请求。

            Returns:
                有待确认停止请求返回 True。
            """
            return self._model.pending_stop_task_id is not None

        @kb.add("q")
        @kb.add("escape", filter=~stop_pending)
        @kb.add("c-c")
        def _exit(event: KeyPressEvent) -> None:
            """退出应用。"""
            event.app.exit()

        @kb.add("tab", filter=~stop_pending)
        def _toggle_filter(event: KeyPressEvent) -> None:
            """切换过滤器。"""
            self._toggle_filter()
            event.app.invalidate()

        @kb.add("r", filter=~stop_pending)
        def _refresh(event: KeyPressEvent) -> None:
            """刷新任务列表。"""
            self._refresh_views()
            event.app.invalidate()

        @kb.add("s", filter=~stop_pending)
        def _stop(event: KeyPressEvent) -> None:
            """请求停止任务。"""
            self._request_stop_for_selected_task()
            event.app.invalidate()

        @kb.add("y", filter=stop_pending)
        def _confirm_stop(event: KeyPressEvent) -> None:
            """确认停止请求。"""
            self._confirm_stop_request()
            event.app.invalidate()

        @kb.add("n", filter=stop_pending)
        @kb.add("escape", filter=stop_pending)
        def _cancel_stop(event: KeyPressEvent) -> None:
            """取消停止请求。"""
            self._cancel_stop_request()
            event.app.invalidate()

        @kb.add("enter", filter=~stop_pending, eager=True)
        @kb.add("o", filter=~stop_pending)
        def _show_output(event: KeyPressEvent) -> None:
            """显示任务输出。"""
            task_id = self._selected_task_id
            if task_id is None:
                self._model.set_message("No task selected.")
                event.app.invalidate()
                return
            self._open_output(event.app, task_id)

        # 处理函数已通过装饰器注册，此处标记为已访问以避免 lint 警告
        _ = (_exit, _toggle_filter, _refresh, _stop, _confirm_stop, _cancel_stop, _show_output)

        body = VSplit(
            [
                Frame(
                    Box(self._task_list, padding=1),
                    title=lambda: f" Tasks [{self._model.filter_mode}] ",
                ),
                HSplit(
                    [
                        Frame(
                            Window(
                                FormattedTextControl(self._detail_fragments),
                                wrap_lines=True,
                            ),
                            title=" Detail ",
                        ),
                        Frame(
                            Window(
                                FormattedTextControl(self._preview_fragments),
                                wrap_lines=True,
                            ),
                            title=" Preview Output ",
                        ),
                    ]
                ),
            ]
        )
        footer = Window(
            FormattedTextControl(self._footer_fragments),
            height=1,
            style="class:footer",
        )
        header = Window(
            FormattedTextControl(self._header_fragments),
            height=1,
            style="class:header",
        )

        return Application(
            layout=Layout(
                HSplit(
                    [
                        header,
                        body,
                        footer,
                    ]
                ),
                focused_element=self._task_list,
            ),
            key_bindings=kb,
            full_screen=True,
            erase_when_done=True,
            style=_task_browser_style(),
            refresh_interval=_AUTO_REFRESH_SECONDS,
            before_render=lambda _app: self._sync_views(),
        )

    def _sync_views(self) -> None:
        """同步任务列表控件与数据模型。"""
        values, selected = self._model.refresh(self._selected_task_id)
        self._task_list.values = values
        self._task_list.current_value = selected
        self._task_list.current_values = [selected]
        for index, (value, _label) in enumerate(values):
            if value == selected:
                self._task_list._selected_index = index  # pyright: ignore[reportPrivateUsage]
                break

    def _header_fragments(self) -> StyleAndTextTuples:
        """获取标题栏内容。

        Returns:
            标题栏格式化文本片段。
        """
        return self._model.summary_fragments()

    def _detail_fragments(self) -> StyleAndTextTuples:
        """获取详情面板内容。

        Returns:
            详情格式化文本片段。
        """
        return [("", self._model.detail_text(self._selected_task_id))]

    def _preview_fragments(self) -> StyleAndTextTuples:
        """获取预览面板内容。

        Returns:
            预览格式化文本片段。
        """
        return [("", self._model.preview_text(self._selected_task_id))]

    def _footer_fragments(self) -> StyleAndTextTuples:
        """获取底部快捷键栏内容。

        Returns:
            底部格式化文本片段。
        """
        return self._model.footer_fragments(self._selected_task_id)


def _build_full_output_renderable(view: TaskView, output: str) -> Panel:
    """构建完整输出显示的 Rich 可渲染对象。

    Args:
        view: 任务视图对象。
        output: 输出文本。

    Returns:
        包含任务信息和输出的 Rich Panel 对象。
    """
    return Panel(
        Group(
            Text(f"Task ID: {view.spec.id}", style="bold"),
            Text(f"Status: {view.runtime.status}"),
            Text(f"Description: {view.spec.description}"),
            Text(""),
            Text(output),
        ),
        title="Background Task Output",
        border_style="cyan",
    )


def _task_sort_key(view: TaskView) -> tuple[int, float]:
    """生成任务排序键。

    活跃任务排在前面，已完成任务按完成时间倒序排列。

    Args:
        view: 任务视图对象。

    Returns:
        排序键元组，第一个元素为优先级，第二个元素为时间戳。
    """
    if not is_terminal_status(view.runtime.status):
        return (0, view.spec.created_at)
    finished_at = view.runtime.finished_at or view.runtime.updated_at or view.spec.created_at
    return (1, -finished_at)


def _task_timing_label(view: TaskView, *, now: float | None = None) -> str | None:
    """生成任务时间标签。

    Args:
        view: 任务视图对象。
        now: 当前时间戳，默认使用实际当前时间。

    Returns:
        时间标签字符串，如 ``running 5s`` 或 ``finished 2 minutes ago``。
    """
    current = now if now is not None else time.time()
    if view.runtime.finished_at is not None:
        return f"finished {format_relative_time(view.runtime.finished_at)}"
    if view.runtime.started_at is not None:
        seconds = max(0, int(current - view.runtime.started_at))
        return f"running {format_duration(seconds)}"
    return f"updated {format_relative_time(view.runtime.updated_at)}"


def _task_browser_style() -> Style:
    """获取任务浏览器样式。

    Returns:
        prompt_toolkit Style 对象。
    """
    from novel_cli.ui.theme import get_task_browser_style

    return get_task_browser_style()
