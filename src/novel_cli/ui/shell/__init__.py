"""Shell 模块：提供交互式终端用户界面。

本模块实现了 Novel CLI 的核心交互式终端界面，包括：
- Shell 类：主交互循环，处理用户输入和智能体运行
- 斜杠命令处理
- 后台任务自动触发机制
- 审批请求的 UI 处理
- MCP 状态显示
- 自动更新检查

主要入口点是 Shell 类，通过 run() 方法启动交互式会话。
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from kosong.chat_provider import APIStatusError, ChatProviderError
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from novel_cli import logger
from novel_cli.background import list_task_views
from novel_cli.notifications import NotificationManager, NotificationWatcher
from novel_cli.soul import LLMNotSet, LLMNotSupported, MaxStepsReached, RunCancelled, Soul, run_soul
from novel_cli.soul.novelsoul import NovelSoul
from novel_cli.ui.shell import update as _update_mod
from novel_cli.ui.shell.console import console
from novel_cli.ui.shell.echo import render_user_echo_text
from novel_cli.ui.shell.mcp_status import render_mcp_prompt
from novel_cli.ui.shell.prompt import (
    CustomPromptSession,
    PromptMode,
    UserInput,
    toast,
)
from novel_cli.ui.shell.replay import replay_recent_history
from novel_cli.ui.shell.slash import registry as shell_slash_registry
from novel_cli.ui.shell.slash import shell_mode_registry
from novel_cli.ui.shell.update import LATEST_VERSION_FILE, UpdateResult, do_update, semver_tuple
from novel_cli.ui.shell.visualize import (
    ApprovalPromptDelegate,
    visualize,
)
from novel_cli.utils.aioqueue import QueueShutDown
from novel_cli.utils.envvar import get_env_bool
from novel_cli.utils.logging import open_original_stderr
from novel_cli.utils.signals import install_sigint_handler
from novel_cli.utils.slashcmd import SlashCommand, SlashCommandCall, parse_slash_command_call
from novel_cli.utils.subprocess_env import get_clean_env
from novel_cli.utils.term import ensure_new_line, ensure_tty_sane
from novel_cli.wire.types import (
    ApprovalRequest,
    ApprovalResponse,
    ContentPart,
    StatusUpdate,
    WireMessage,
)


@dataclass(slots=True)
class _PromptEvent:
    """提示事件：表示用户输入或系统事件的数据结构。

    用于在事件队列中传递各种类型的提示事件。

    Attributes:
        kind: 事件类型，如 'input'、'interrupt'、'eof'、'error'、'bg_noop'、'input_activity'。
        user_input: 用户输入对象，仅在 kind 为 'input' 时有效。
    """

    kind: str
    user_input: UserInput | None = None


_MAX_BG_AUTO_TRIGGER_FAILURES = 3
"""连续失败后停止自动触发的最大次数。"""

_BG_AUTO_TRIGGER_INPUT_GRACE_S = 0.75
"""后台自动触发在本地提示活动后的延迟时间（秒）。"""


class _BackgroundCompletionWatcher:
    """后台任务完成监视器：监视后台任务完成并自动触发智能体。

    位于空闲事件循环和智能体之间：当智能体空闲时，后台任务完成
    且 LLM 尚未消费通知，则触发智能体运行。

    重要提示：仅存在待处理通知不应在会话恢复时立即触发前台运行。
    它们会被下一个实际的后台完成信号或下一次用户触发的回合消费。

    Attributes:
        _event: 后台任务完成事件。
        _notifications: 通知管理器。
        _can_auto_trigger_pending: 判断是否可以自动触发待处理通知的回调函数。
    """

    def __init__(
        self,
        soul: Soul,
        *,
        can_auto_trigger_pending: Callable[[], bool] | None = None,
    ) -> None:
        """初始化后台任务完成监视器。

        Args:
            soul: 智能体实例。
            can_auto_trigger_pending: 判断是否可以自动触发待处理通知的回调函数，
                默认为始终返回 True。
        """
        self._event: asyncio.Event | None = None
        self._notifications: NotificationManager | None = None
        self._can_auto_trigger_pending = can_auto_trigger_pending or (lambda: True)
        if isinstance(soul, NovelSoul):
            self._event = soul.runtime.background_tasks.completion_event
            self._notifications = soul.runtime.notifications

    @property
    def enabled(self) -> bool:
        """检查监视器是否启用。

        Returns:
            如果后台任务完成事件存在则返回 True，否则返回 False。
        """
        return self._event is not None

    def clear(self) -> None:
        """清除上次智能体运行遗留的过期信号。"""
        if self._event is not None:
            self._event.clear()

    async def wait_for_next(self, idle_events: asyncio.Queue[_PromptEvent]) -> _PromptEvent | None:
        """等待下一个用户提示事件或后台任务完成。

        如果用户输入先到达则返回提示事件，如果后台任务完成且存在未消费的
        LLM 通知则返回 None。用户输入始终优先于后台任务完成。

        Args:
            idle_events: 空闲事件队列。

        Returns:
            提示事件对象，或 None 表示应触发后台智能体运行。
        """
        if self.enabled and self._has_pending_llm_notifications():
            # 存在待处理通知（例如恢复会话后）
            # 在用户发送恢复后第一个前台回合之前，
            # 待处理的后台通知不应自动触发运行
            # 一旦用户触发的回合激活了 shell，待处理通知可以恢复正常的自动跟进行为
            try:
                return idle_events.get_nowait()
            except asyncio.QueueEmpty:
                if self._can_auto_trigger_pending():
                    return None

        idle_task = asyncio.create_task(idle_events.get())
        if not self.enabled:
            return await idle_task

        assert self._event is not None
        bg_wait_task = asyncio.create_task(self._event.wait())

        done, _ = await asyncio.wait(
            [idle_task, bg_wait_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in (idle_task, bg_wait_task):
            if t not in done:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

        if idle_task in done:
            if bg_wait_task in done:
                self._event.clear()
            return idle_task.result()

        # 仅后台事件触发
        self._event.clear()
        if self._has_pending_llm_notifications():
            if self._can_auto_trigger_pending():
                return None
            return _PromptEvent(kind="bg_noop")
        return _PromptEvent(kind="bg_noop")

    def _has_pending_llm_notifications(self) -> bool:
        """检查是否存在待处理的 LLM 通知。

        Returns:
            如果存在待处理的 LLM 通知则返回 True，否则返回 False。
        """
        if self._notifications is None:
            return False
        return self._notifications.has_pending_for_sink("llm")


class _BackgroundAutoTriggerPromptState(Protocol):
    """后台自动触发提示状态协议：定义提示会话状态检查接口。

    用于检查提示会话是否有待处理输入或近期输入活动。
    """

    def has_pending_input(self) -> bool: ...

    def had_recent_input_activity(self, *, within_s: float) -> bool: ...

    def recent_input_activity_remaining(self, *, within_s: float) -> float: ...

    async def wait_for_input_activity(self) -> None: ...


class Shell:
    """Shell 类：交互式终端用户界面的核心实现。

    提供主交互循环，处理用户输入、智能体运行、斜杠命令执行、
    后台任务管理和审批请求处理等功能。

    Attributes:
        soul: 智能体实例。
        _welcome_info: 欢迎信息列表。
        _prefill_text: 预填充文本。
        _background_tasks: 后台任务集合。
        _prompt_session: 自定义提示会话。
        _running_input_handler: 运行中的输入处理器。
        _running_interrupt_handler: 运行中的中断处理器。
        _active_approval_sink: 活动的审批接收器。
        _pending_approval_requests: 待处理的审批请求队列。
        _current_prompt_approval_request: 当前提示中的审批请求。
        _approval_modal: 审批模态框委托。
        _exit_after_run: 运行后退出标志。
        _available_slash_commands: 可用的斜杠命令字典。
    """

    def __init__(
        self,
        soul: Soul,
        welcome_info: list[WelcomeInfoItem] | None = None,
        prefill_text: str | None = None,
    ):
        """初始化 Shell 实例。

        Args:
            soul: 智能体实例。
            welcome_info: 欢迎信息列表，可选。
            prefill_text: 预填充到输入框的文本，可选。
        """
        self.soul = soul
        self._welcome_info = list(welcome_info or [])
        self._prefill_text = prefill_text
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._prompt_session: CustomPromptSession | None = None
        self._running_input_handler: Callable[[UserInput], None] | None = None
        self._running_interrupt_handler: Callable[[], None] | None = None
        self._active_approval_sink: Any | None = None
        self._pending_approval_requests = deque[ApprovalRequest]()
        self._current_prompt_approval_request: ApprovalRequest | None = None
        self._approval_modal: ApprovalPromptDelegate | None = None
        self._exit_after_run = False
        self._available_slash_commands: dict[str, SlashCommand[Any]] = {
            **{cmd.name: cmd for cmd in soul.available_slash_commands},
            **{cmd.name: cmd for cmd in shell_slash_registry.list_commands()},
        }
        """Shell 级斜杠命令 + 智能体级斜杠命令。名称到命令的映射。"""

    @property
    def available_slash_commands(self) -> dict[str, SlashCommand[Any]]:
        """获取所有可用的斜杠命令，包括 Shell 级和智能体级命令。

        Returns:
            斜杠命令名称到命令对象的映射字典。
        """
        return self._available_slash_commands

    @staticmethod
    def _should_exit_input(user_input: UserInput) -> bool:
        """判断用户输入是否表示退出命令。

        Args:
            user_input: 用户输入对象。

        Returns:
            如果输入为 exit、quit、/exit 或 /quit 则返回 True。
        """
        return user_input.command.strip() in {"exit", "quit", "/exit", "/quit"}

    @staticmethod
    def _agent_slash_command_call(user_input: UserInput) -> SlashCommandCall | None:
        """从用户输入解析智能体模式下的斜杠命令调用。

        Args:
            user_input: 用户输入对象。

        Returns:
            斜杠命令调用对象，如果不是斜杠命令则返回 None。
        """
        if user_input.mode != PromptMode.AGENT:
            return None
        display_call = parse_slash_command_call(user_input.command)
        if display_call is None:
            return None
        resolved_call = parse_slash_command_call(user_input.resolved_command)
        if resolved_call is None or resolved_call.name != display_call.name:
            return display_call
        return resolved_call

    @staticmethod
    def _should_echo_agent_input(user_input: UserInput) -> bool:
        """判断是否应该回显智能体模式的用户输入。

        Args:
            user_input: 用户输入对象。

        Returns:
            如果应该回显则返回 True。
        """
        if user_input.mode != PromptMode.AGENT:
            return False
        if Shell._should_exit_input(user_input):
            return False
        return Shell._agent_slash_command_call(user_input) is None

    @staticmethod
    def _echo_agent_input(user_input: UserInput) -> None:
        """回显智能体模式的用户输入。

        Args:
            user_input: 用户输入对象。
        """
        console.print(render_user_echo_text(user_input.command))

    def _bind_running_input(
        self,
        on_input: Callable[[UserInput], None],
        on_interrupt: Callable[[], None],
    ) -> None:
        """绑定运行中的输入处理器和中断处理器。

        Args:
            on_input: 输入处理回调函数。
            on_interrupt: 中断处理回调函数。
        """
        self._running_input_handler = on_input
        self._running_interrupt_handler = on_interrupt

    def _unbind_running_input(self) -> None:
        """解绑运行中的输入处理器和中断处理器。"""
        self._running_input_handler = None
        self._running_interrupt_handler = None

    async def _route_prompt_events(
        self,
        prompt_session: CustomPromptSession,
        idle_events: asyncio.Queue[_PromptEvent],
        resume_prompt: asyncio.Event,
    ) -> None:
        """路由提示事件：持续读取用户输入并发送到事件队列。

        保持恰好一个活动的提示读取。空闲提交会暂停路由器，
        直到 Shell 决定下一个提示应该等待阻塞操作还是
        在智能体运行期间保持活动状态。

        Args:
            prompt_session: 自定义提示会话。
            idle_events: 空闲事件队列。
            resume_prompt: 恢复提示事件，控制提示路由器是否读取输入。
        """
        while True:
            # 保持恰好一个活动的提示读取。空闲提交会暂停路由器，
            # 直到 Shell 决定下一个提示应该等待阻塞操作
            # 还是保持活动状态（允许转向输入的智能体运行）
            await resume_prompt.wait()
            ensure_tty_sane()
            try:
                ensure_new_line()
                user_input = await prompt_session.prompt_next()
            except KeyboardInterrupt:
                logger.debug("提示路由器收到 KeyboardInterrupt")
                if (
                    self._running_input_handler is not None
                    and prompt_session.running_prompt_accepts_submission()
                ):
                    if self._running_interrupt_handler is not None:
                        self._running_interrupt_handler()
                    continue
                resume_prompt.clear()
                await idle_events.put(_PromptEvent(kind="interrupt"))
                continue
            except EOFError:
                logger.debug("提示路由器收到 EOF")
                if (
                    self._running_input_handler is not None
                    and prompt_session.running_prompt_accepts_submission()
                ):
                    self._exit_after_run = True
                    if self._running_interrupt_handler is not None:
                        self._running_interrupt_handler()
                    return
                resume_prompt.clear()
                await idle_events.put(_PromptEvent(kind="eof"))
                return
            except Exception:
                logger.exception("提示路由器崩溃")
                resume_prompt.clear()
                await idle_events.put(_PromptEvent(kind="error"))
                return

            if prompt_session.last_submission_was_running:  # noqa: SIM102
                if self._running_input_handler is not None:
                    if user_input:
                        self._running_input_handler(user_input)
                    continue
                # 处理器已解绑 —— 转入空闲路径

            resume_prompt.clear()
            await idle_events.put(_PromptEvent(kind="input", user_input=user_input))

    async def run(self, command: str | None = None) -> bool:
        """运行 Shell 交互循环。

        Args:
            command: 可选的单次执行命令，如果提供则执行后退出。

        Returns:
            返回 True 表示 Shell 正常结束，False 表示发生错误。
        """
        # 从配置初始化主题
        if isinstance(self.soul, NovelSoul):
            from novel_cli.ui.theme import set_active_theme

            set_active_theme(self.soul.runtime.config.theme)

        if command is not None:
            # 运行单次命令后退出
            logger.info("运行智能体命令: {command}", command=command)
            if isinstance(self.soul, NovelSoul):
                self._start_background_task(self._watch_root_wire_hub())
            try:
                return await self.run_soul_command(command)
            finally:
                self._cancel_background_tasks()

        # 启动自动更新后台任务（除非被禁用）
        if get_env_bool("NOVEL_CLI_NO_AUTO_UPDATE"):
            logger.info("自动更新已被 NOVEL_CLI_NO_AUTO_UPDATE 环境变量禁用")
        else:
            self._start_background_task(self._auto_update())

        _print_welcome_info(self.soul.name or "Novel CLI", self._welcome_info)

        if isinstance(self.soul, NovelSoul):
            watcher = NotificationWatcher(
                self.soul.runtime.notifications,
                sink="shell",
                before_poll=self.soul.runtime.background_tasks.reconcile,
                on_notification=lambda notification: toast(
                    f"[{notification.event.type}] {notification.event.title}",
                    topic="notification",
                    duration=10.0,
                ),
            )
            self._start_background_task(watcher.run_forever())
            self._start_background_task(self._watch_root_wire_hub())
            await replay_recent_history(
                self.soul.context.history,
                wire_file=self.soul.wire_file,
            )
            await self.soul.start_background_mcp_loading()

        async def _plan_mode_toggle() -> bool:
            if isinstance(self.soul, NovelSoul):
                return await self.soul.toggle_plan_mode_from_manual()
            return False

        def _mcp_status_block(columns: int):
            if not isinstance(self.soul, NovelSoul):
                return None
            snapshot = self.soul.status.mcp_status
            if snapshot is None:
                return None
            return render_mcp_prompt(snapshot)

        def _mcp_status_loading() -> bool:
            if not isinstance(self.soul, NovelSoul):
                return False
            snapshot = self.soul.status.mcp_status
            return bool(snapshot and snapshot.loading)

        @dataclass
        class _BgCountCache:
            time: float = 0.0
            count: int = 0

        _bg_cache = _BgCountCache()

        def _bg_task_count() -> int:
            if not isinstance(self.soul, NovelSoul):
                return 0
            now = time.monotonic()
            if now - _bg_cache.time < 1.0:
                return _bg_cache.count
            views = list_task_views(self.soul.runtime.background_tasks, active_only=True)
            _bg_cache.count = sum(1 for v in views if v.spec.kind == "bash")
            _bg_cache.time = now
            return _bg_cache.count

        with CustomPromptSession(
            status_provider=lambda: self.soul.status,
            status_block_provider=_mcp_status_block,
            fast_refresh_provider=_mcp_status_loading,
            background_task_count_provider=_bg_task_count,
            model_capabilities=self.soul.model_capabilities or set(),
            model_name=self.soul.model_name,
            thinking=self.soul.thinking or False,
            agent_mode_slash_commands=list(self._available_slash_commands.values()),
            shell_mode_slash_commands=shell_mode_registry.list_commands(),
            editor_command_provider=lambda: (
                self.soul.runtime.config.default_editor if isinstance(self.soul, NovelSoul) else ""
            ),
            plan_mode_toggle_callback=_plan_mode_toggle,
            current_book_provider=lambda: (
                self.soul.runtime.session.state.current_book
                if isinstance(self.soul, NovelSoul) else None
            ),
        ) as prompt_session:
            self._prompt_session = prompt_session
            if self._prefill_text:
                prompt_session.set_prefill_text(self._prefill_text)
                self._prefill_text = None
            if isinstance(self.soul, NovelSoul):
                novel_soul = self.soul
                snapshot = novel_soul.status.mcp_status
                if snapshot and snapshot.loading:

                    async def _invalidate_after_mcp_loading() -> None:
                        try:
                            await novel_soul.wait_for_background_mcp_loading()
                        except Exception:
                            logger.debug("MCP loading finished with error while refreshing prompt")
                        if self._prompt_session is prompt_session:
                            prompt_session.invalidate()

                    self._start_background_task(_invalidate_after_mcp_loading())
            self._exit_after_run = False
            idle_events: asyncio.Queue[_PromptEvent] = asyncio.Queue()
            # resume_prompt controls whether the prompt router reads input.
            # Set BEFORE an await = prompt stays live during the operation
            # (agent runs that accept steer input); set AFTER = prompt is
            # paused until the operation finishes.
            resume_prompt = asyncio.Event()
            resume_prompt.set()
            prompt_task = asyncio.create_task(
                self._route_prompt_events(prompt_session, idle_events, resume_prompt)
            )
            background_autotrigger_armed = False

            def _can_auto_trigger_pending() -> bool:
                return background_autotrigger_armed

            bg_watcher = _BackgroundCompletionWatcher(
                self.soul,
                can_auto_trigger_pending=_can_auto_trigger_pending,
            )

            shell_ok = True
            bg_auto_failures = 0
            deferred_bg_trigger = False
            try:
                while True:
                    if deferred_bg_trigger and not self._should_defer_background_auto_trigger(
                        prompt_session
                    ):
                        result = None
                    elif deferred_bg_trigger:
                        result = await self._wait_for_input_or_activity(
                            prompt_session,
                            idle_events,
                            timeout_s=self._background_auto_trigger_timeout_s(prompt_session),
                        )
                    else:
                        bg_watcher.clear()
                        if bg_auto_failures >= _MAX_BG_AUTO_TRIGGER_FAILURES:
                            result = await idle_events.get()
                        else:
                            result = await bg_watcher.wait_for_next(idle_events)

                    if result is None:
                        if self._should_defer_background_auto_trigger(prompt_session):
                            deferred_bg_trigger = True
                            resume_prompt.set()
                            continue
                        deferred_bg_trigger = False
                        logger.info("Background task completed while idle, triggering agent")
                        resume_prompt.set()
                        ok = await self.run_soul_command(
                            "<system-reminder>"
                            "Background tasks completed while you"
                            " were idle."
                            "</system-reminder>"
                        )
                        console.print()
                        if not ok:
                            bg_auto_failures += 1
                            logger.warning(
                                "Background auto-trigger failed ({n}/{max})",
                                n=bg_auto_failures,
                                max=_MAX_BG_AUTO_TRIGGER_FAILURES,
                            )
                        else:
                            bg_auto_failures = 0
                        if self._exit_after_run:
                            console.print("Bye!")
                            break
                        continue

                    event = result

                    if event.kind == "input_activity":
                        continue

                    if event.kind == "bg_noop":
                        continue

                    if event.kind == "interrupt":
                        console.print("[grey50]Tip: press Ctrl-D or send 'exit' to quit[/grey50]")
                        resume_prompt.set()
                        continue

                    if event.kind == "eof":
                        console.print("Bye!")
                        break

                    if event.kind == "error":
                        shell_ok = False
                        break

                    user_input = event.user_input
                    assert user_input is not None
                    bg_auto_failures = 0
                    deferred_bg_trigger = False
                    if not user_input:
                        logger.debug("Got empty input, skipping")
                        resume_prompt.set()
                        continue
                    logger.debug("Got user input: {user_input}", user_input=user_input)

                    if self._should_echo_agent_input(user_input):
                        self._echo_agent_input(user_input)

                    if self._should_exit_input(user_input):
                        logger.debug("Exiting by slash command")
                        console.print("Bye!")
                        break

                    if user_input.mode == PromptMode.SHELL:
                        await self._run_shell_command(user_input.command)
                        resume_prompt.set()
                        continue

                    if slash_cmd_call := self._agent_slash_command_call(user_input):
                        is_soul_slash = (
                            slash_cmd_call.name in self._available_slash_commands
                            and shell_slash_registry.find_command(slash_cmd_call.name) is None
                        )
                        if is_soul_slash:
                            background_autotrigger_armed = True
                            resume_prompt.set()
                            await self.run_soul_command(slash_cmd_call.raw_input)
                            console.print()
                            if self._exit_after_run:
                                console.print("Bye!")
                                break
                        else:
                            await self._run_slash_command(slash_cmd_call)
                            resume_prompt.set()
                        continue

                    background_autotrigger_armed = True
                    resume_prompt.set()
                    await self.run_soul_command(user_input.content)
                    console.print()
                    if self._exit_after_run:
                        console.print("Bye!")
                        break
            finally:
                prompt_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prompt_task
                self._running_input_handler = None
                self._running_interrupt_handler = None
                if self._prompt_session is prompt_session and self._approval_modal is not None:
                    prompt_session.detach_modal(self._approval_modal)
                    self._approval_modal = None
                self._prompt_session = None
                self._cancel_background_tasks()
                ensure_tty_sane()

        return shell_ok

    async def _run_shell_command(self, command: str) -> None:
        """在前台运行 Shell 命令。

        Args:
            command: 要执行的 Shell 命令字符串。
        """
        if not command.strip():
            return

        # 检查是否是 Shell 模式下允许的斜杠命令
        if slash_cmd_call := parse_slash_command_call(command):
            if shell_mode_registry.find_command(slash_cmd_call.name):
                await self._run_slash_command(slash_cmd_call)
                return
            else:
                console.print(
                    f'[yellow]"/{slash_cmd_call.name}" 在 Shell 模式下不可用。'
                    "按 Ctrl-X 切换到智能体模式。[yellow]"
                )
                return

        # 检查用户是否尝试使用 'cd' 命令
        stripped_cmd = command.strip()
        split_cmd: list[str] | None = None
        try:
            split_cmd = shlex.split(stripped_cmd)
        except ValueError as exc:
            logger.debug("解析 Shell 命令失败（用于 cd 检查）: {error}", error=exc)
        if split_cmd and len(split_cmd) == 2 and split_cmd[0] == "cd":
            console.print(
                "[yellow]警告：目录更改不会在命令执行之间保留。[/yellow]"
            )
            return

        logger.info("运行 Shell 命令: {cmd}", cmd=command)

        proc: asyncio.subprocess.Process | None = None

        def _handler():
            logger.debug("收到 SIGINT 信号")
            if proc:
                proc.terminate()

        loop = asyncio.get_running_loop()
        remove_sigint = install_sigint_handler(loop, _handler)
        try:
            # TODO: 为了简单起见，目前使用 `create_subprocess_shell`
            # 后续应考虑使其行为更像真正的 Shell
            with open_original_stderr() as stderr:
                kwargs: dict[str, Any] = {}
                if stderr is not None:
                    kwargs["stderr"] = stderr
                proc = await asyncio.create_subprocess_shell(command, env=get_clean_env(), **kwargs)
                await proc.wait()
        except Exception as e:
            logger.exception("运行 Shell 命令失败:")
            console.print(f"[red]运行 Shell 命令失败: {e}[/red]")
        finally:
            remove_sigint()

    async def _run_slash_command(self, command_call: SlashCommandCall) -> None:
        """运行斜杠命令。

        Args:
            command_call: 斜杠命令调用对象。

        Raises:
            Reload: 当需要重新加载会话时抛出。
            SwitchToWeb: 当需要切换到 Web UI 时抛出。
            SwitchToVis: 当需要切换到可视化界面时抛出。
        """
        from novel_cli.cli import Reload, SwitchToVis, SwitchToWeb

        if command_call.name not in self._available_slash_commands:
            logger.info("未知斜杠命令 /{command}", command=command_call.name)
            console.print(
                f'[red]未知斜杠命令 "/{command_call.name}"，'
                '输入 "/" 查看所有可用命令[/red]'
            )
            return

        command = shell_slash_registry.find_command(command_call.name)
        if command is None:
            # 输入是智能体级斜杠命令调用
            await self.run_soul_command(command_call.raw_input)
            return

        logger.debug(
            "运行 Shell 级斜杠命令: /{command}，参数: {args}",
            command=command_call.name,
            args=command_call.args,
        )

        try:
            ret = command.func(self, command_call.args)
            if isinstance(ret, Awaitable):
                await ret
        except (Reload, SwitchToWeb, SwitchToVis):
            # 直接传播
            raise
        except (asyncio.CancelledError, KeyboardInterrupt):
            # 处理斜杠命令执行期间的 Ctrl-C，返回到 Shell 提示
            logger.debug("斜杠命令被 KeyboardInterrupt 中断")
            console.print("[red]被用户中断[/red]")
        except Exception as e:
            logger.exception("未知错误:")
            console.print(f"[red]未知错误: {e}[/red]")
            raise  # 重新抛出未知错误

    async def run_soul_command(self, user_input: str | list[ContentPart]) -> bool:
        """运行智能体并处理已知异常。

        Args:
            user_input: 用户输入，可以是字符串或内容部分列表。

        Returns:
            返回 True 表示运行成功，False 表示运行失败。
        """
        logger.info("运行智能体，用户输入: {user_input}", user_input=user_input)

        cancel_event = asyncio.Event()

        def _handler():
            logger.debug("收到 SIGINT 信号")
            cancel_event.set()

        loop = asyncio.get_running_loop()
        remove_sigint = install_sigint_handler(loop, _handler)

        try:
            snap = self.soul.status
            runtime = self.soul.runtime if isinstance(self.soul, NovelSoul) else None
            await run_soul(
                self.soul,
                user_input,
                lambda wire: visualize(
                    wire.ui_side(merge=False),  # Shell UI 维护自己的合并缓冲区
                    initial_status=StatusUpdate(
                        context_usage=snap.context_usage,
                        context_tokens=snap.context_tokens,
                        max_context_tokens=snap.max_context_tokens,
                        mcp_status=snap.mcp_status,
                    ),
                    cancel_event=cancel_event,
                    prompt_session=self._prompt_session,
                    steer=self.soul.steer if isinstance(self.soul, NovelSoul) else None,
                    bind_running_input=self._bind_running_input,
                    unbind_running_input=self._unbind_running_input,
                    on_view_ready=self._set_active_approval_sink,
                    on_view_closed=self._clear_active_approval_sink,
                ),
                cancel_event,
                runtime.session.wire_file if runtime else None,
                runtime,
            )
            return True
        except LLMNotSet:
            logger.exception("LLM 未设置:")
            console.print("[red]LLM 未设置，请运行 'novel setup' 进行配置[/red]")
        except LLMNotSupported as e:
            # 实际不支持的模式应该已经被提示会话阻止
            logger.exception("LLM 不支持:")
            console.print(f"[red]{e}[/red]")
        except ChatProviderError as e:
            logger.exception("LLM 提供者错误:")
            if isinstance(e, APIStatusError) and e.status_code == 401:
                console.print("[red]授权失败，请检查登录状态[/red]")
            elif isinstance(e, APIStatusError) and e.status_code == 402:
                console.print("[red]会员已过期，请续费[/red]")
            elif isinstance(e, APIStatusError) and e.status_code == 403:
                console.print("[red]配额超限，请升级套餐或稍后重试[/red]")
            else:
                console.print(f"[red]LLM 提供者错误: {e}[/red]")
        except MaxStepsReached as e:
            logger.warning("达到最大步数: {n_steps}", n_steps=e.n_steps)
            console.print(f"[yellow]{e}[/yellow]")
        except RunCancelled:
            logger.info("被用户取消")
            console.print("[red]被用户中断[/red]")
        except Exception as e:
            logger.exception("意外错误:")
            console.print(f"[red]意外错误: {e}[/red]")
            raise  # 重新抛出未知错误
        finally:
            self._maybe_present_pending_approvals()
            remove_sigint()
        return False

    @staticmethod
    def _should_defer_background_auto_trigger(
        prompt_session: _BackgroundAutoTriggerPromptState | None,
    ) -> bool:
        """判断是否应该推迟后台自动触发。

        Args:
            prompt_session: 提示会话状态对象。

        Returns:
            如果存在待处理输入或近期输入活动则返回 True。
        """
        if prompt_session is None:
            return False
        return prompt_session.has_pending_input() or prompt_session.had_recent_input_activity(
            within_s=_BG_AUTO_TRIGGER_INPUT_GRACE_S
        )

    @staticmethod
    def _background_auto_trigger_timeout_s(
        prompt_session: _BackgroundAutoTriggerPromptState | None,
    ) -> float | None:
        """获取后台自动触发的超时时间（秒）。

        Args:
            prompt_session: 提示会话状态对象。

        Returns:
            超时时间（秒），如果无需等待则返回 None。
        """
        if prompt_session is None or prompt_session.has_pending_input():
            return None
        remaining = prompt_session.recent_input_activity_remaining(
            within_s=_BG_AUTO_TRIGGER_INPUT_GRACE_S
        )
        return remaining if remaining > 0 else None

    async def _wait_for_input_or_activity(
        self,
        prompt_session: _BackgroundAutoTriggerPromptState,
        idle_events: asyncio.Queue[_PromptEvent],
        *,
        timeout_s: float | None = None,
    ) -> _PromptEvent:
        """等待用户输入或输入活动。

        Args:
            prompt_session: 提示会话状态对象。
            idle_events: 空闲事件队列。
            timeout_s: 超时时间（秒），可选。

        Returns:
            提示事件对象。
        """
        idle_task = asyncio.create_task(idle_events.get())
        activity_task = asyncio.create_task(prompt_session.wait_for_input_activity())
        timeout_task = (
            asyncio.create_task(asyncio.sleep(timeout_s)) if timeout_s is not None else None
        )
        done: set[asyncio.Task[Any]] = set()
        try:
            done, _ = await asyncio.wait(
                [task for task in (idle_task, activity_task, timeout_task) if task is not None],
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (idle_task, activity_task, timeout_task):
                if task is None:
                    continue
                if task.done():
                    continue
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        if idle_task in done:
            return idle_task.result()
        return _PromptEvent(kind="input_activity")

    async def _watch_root_wire_hub(self) -> None:
        """监视根 Wire Hub 的消息。"""
        if not isinstance(self.soul, NovelSoul):
            return
        if self.soul.runtime.root_wire_hub is None:
            return
        queue = self.soul.runtime.root_wire_hub.subscribe()
        try:
            while True:
                try:
                    msg = await queue.get()
                except QueueShutDown:
                    return
                try:
                    await self._handle_root_hub_message(msg)
                except Exception:
                    logger.exception("处理根 Hub 消息失败:")
        finally:
            self.soul.runtime.root_wire_hub.unsubscribe(queue)

    async def _handle_root_hub_message(self, msg: WireMessage) -> None:
        """处理根 Hub 消息。

        Args:
            msg: Wire 消息对象。
        """
        if not isinstance(self.soul, NovelSoul):
            return
        match msg:
            case ApprovalRequest() as request:
                request = self._enrich_approval_request_for_ui(request)
                if self.soul.runtime.approval_runtime is None:
                    return
                record = self.soul.runtime.approval_runtime.get_request(request.id)
                if record is None or record.status != "pending":
                    return
                if self._prompt_session is not None:
                    # 交互模式：通过模态框排队和呈现
                    self._queue_approval_request(request)
                    self._maybe_present_pending_approvals()
                    self._prompt_session.invalidate()
                elif self._active_approval_sink is not None:
                    # 非交互模式且有实时视图：转发到接收器
                    self._forward_approval_to_sink(request)
                else:
                    # 稍后排队处理
                    self._queue_approval_request(request)
            case ApprovalResponse() as response:
                # 外部解决（例如来自 Web UI）
                if (
                    self._approval_modal is not None
                    and self._approval_modal.request.id == response.request_id
                ):
                    if not self._approval_modal.request.resolved:
                        self._approval_modal.request.resolve(response.response)
                    self._clear_current_prompt_approval_request(response.request_id)
                    self._activate_prompt_approval_modal()
                self._remove_pending_approval_request(response.request_id)
                self._maybe_present_pending_approvals()
                if self._prompt_session is not None:
                    self._prompt_session.invalidate()
            case _:
                return

    def _enrich_approval_request_for_ui(self, request: ApprovalRequest) -> ApprovalRequest:
        """为 UI 丰富审批请求信息。

        Args:
            request: 原始审批请求。

        Returns:
            丰富后的审批请求。
        """
        if not isinstance(self.soul, NovelSoul):
            return request
        if request.agent_id is None:
            return request
        if self.soul.runtime.subagent_store is None:
            return request
        record = self.soul.runtime.subagent_store.get_instance(request.agent_id)
        if record is None:
            return request
        return request.model_copy(update={"source_description": record.description})

    def _set_active_approval_sink(self, sink: Any) -> None:
        """设置活动的审批接收器。

        Args:
            sink: 审批接收器对象。
        """
        self._active_approval_sink = sink
        # 交互模式下，审批由提示模态框处理，而非实时视图接收器
        # 不刷新以避免丢失请求
        if self._prompt_session is not None:
            return
        # 将待处理审批刷新到新激活的接收器
        while self._pending_approval_requests:
            request = self._pending_approval_requests.popleft()

            if not isinstance(self.soul, NovelSoul) or self.soul.runtime.approval_runtime is None:
                break
            record = self.soul.runtime.approval_runtime.get_request(request.id)
            if record is None or record.status != "pending":
                continue
            self._forward_approval_to_sink(request)

    def _clear_active_approval_sink(self) -> None:
        """清除活动的审批接收器。"""
        self._active_approval_sink = None
        # 重新排队已转发到接收器但尚未解决的审批请求
        # 否则这些请求在回合间的实时视图关闭时会静默丢失
        if not isinstance(self.soul, NovelSoul) or self.soul.runtime.approval_runtime is None:
            return
        for record in self.soul.runtime.approval_runtime.list_pending():
            self._queue_approval_request(
                self._enrich_approval_request_for_ui(
                    ApprovalRequest(
                        id=record.id,
                        tool_call_id=record.tool_call_id,
                        sender=record.sender,
                        action=record.action,
                        description=record.description,
                        display=record.display,
                        source_kind=record.source.kind,
                        source_id=record.source.id,
                        agent_id=record.source.agent_id,
                        subagent_type=record.source.subagent_type,
                    )
                )
            )

    def _forward_approval_to_sink(self, request: ApprovalRequest) -> None:
        """将审批请求转发到活动的实时视图接收器并桥接响应。

        Args:
            request: 审批请求对象。
        """
        if self._active_approval_sink is None:
            self._queue_approval_request(request)
            return
        self._active_approval_sink.enqueue_external_message(request)

        async def _bridge() -> None:
            try:
                response = await request.wait()
                if (
                    isinstance(self.soul, NovelSoul)
                    and self.soul.runtime.approval_runtime is not None
                ):
                    self.soul.runtime.approval_runtime.resolve(
                        request.id, response, feedback=request.feedback
                    )
            finally:
                if self._prompt_session is not None:
                    self._prompt_session.invalidate()

        self._start_background_task(_bridge())

    def _queue_approval_request(self, request: ApprovalRequest) -> None:
        """将审批请求加入队列。

        Args:
            request: 审批请求对象。
        """
        if self._approval_modal is not None and self._approval_modal.request.id == request.id:
            return
        if (
            self._current_prompt_approval_request is not None
            and self._current_prompt_approval_request.id == request.id
        ):
            return
        if any(r.id == request.id for r in self._pending_approval_requests):
            return
        self._pending_approval_requests.append(request)

    def _remove_pending_approval_request(self, request_id: str) -> None:
        """从队列中移除待处理的审批请求。

        Args:
            request_id: 审批请求 ID。
        """
        self._clear_current_prompt_approval_request(request_id)
        self._pending_approval_requests = deque(
            r for r in self._pending_approval_requests if r.id != request_id
        )

    def _clear_current_prompt_approval_request(self, request_id: str) -> None:
        """清除当前提示中的审批请求。

        Args:
            request_id: 审批请求 ID。
        """
        if (
            self._current_prompt_approval_request is not None
            and self._current_prompt_approval_request.id == request_id
        ):
            self._current_prompt_approval_request = None

    def _maybe_present_pending_approvals(self) -> None:
        """可能呈现待处理的审批请求。"""
        if self._prompt_session is not None:
            self._activate_prompt_approval_modal()
            return
        if self._active_approval_sink is not None:
            while self._pending_approval_requests:
                request = self._pending_approval_requests.popleft()

                if not isinstance(self.soul, NovelSoul):
                    break
                if self.soul.runtime.approval_runtime is None:
                    break
                record = self.soul.runtime.approval_runtime.get_request(request.id)
                if record is None or record.status != "pending":
                    continue
                self._forward_approval_to_sink(request)

    def _activate_prompt_approval_modal(self) -> None:
        """激活提示审批模态框。"""
        if self._prompt_session is None:
            return
        current_request = self._current_prompt_approval_request
        if current_request is None:
            current_request = self._pop_next_pending_approval_request()
            self._current_prompt_approval_request = current_request
        if current_request is None:
            if self._approval_modal is not None:
                self._prompt_session.detach_modal(self._approval_modal)
                self._approval_modal = None
            return
        if self._approval_modal is None:
            self._approval_modal = ApprovalPromptDelegate(
                current_request,
                on_response=self._handle_prompt_approval_response,
                buffer_text_provider=(
                    lambda: self._prompt_session._session.default_buffer.text  # pyright: ignore[reportPrivateUsage]
                    if self._prompt_session is not None
                    else ""
                ),
                text_expander=self._prompt_session._get_placeholder_manager().serialize_for_history,  # pyright: ignore[reportPrivateUsage]
            )
            self._prompt_session.attach_modal(self._approval_modal)
        else:
            if self._approval_modal.request.id != current_request.id:
                self._approval_modal.set_request(current_request)
        self._prompt_session.invalidate()

    def _handle_prompt_approval_response(
        self,
        request: ApprovalRequest,
        response: ApprovalResponse.Kind,
        feedback: str = "",
    ) -> None:
        """处理提示审批响应。

        Args:
            request: 审批请求对象。
            response: 审批响应类型。
            feedback: 用户反馈文本，可选。
        """
        if not isinstance(self.soul, NovelSoul):
            return
        if self.soul.runtime.approval_runtime is None:
            return
        self.soul.runtime.approval_runtime.resolve(request.id, response, feedback=feedback)
        self._clear_current_prompt_approval_request(request.id)
        self._activate_prompt_approval_modal()

    def _pop_next_pending_approval_request(self) -> ApprovalRequest | None:
        """弹出下一个待处理的审批请求。

        Returns:
            下一个待处理的审批请求，如果没有则返回 None。
        """
        if not isinstance(self.soul, NovelSoul) or self.soul.runtime.approval_runtime is None:
            return None
        while self._pending_approval_requests:
            request = self._pending_approval_requests.popleft()

            record = self.soul.runtime.approval_runtime.get_request(request.id)
            if record is None or record.status != "pending":
                continue
            return request
        return None

    async def _auto_update(self) -> None:
        """自动更新检查任务。"""
        result = await do_update(print=False, check_only=True)
        if result == UpdateResult.UPDATE_AVAILABLE:
            while True:
                toast(
                    f"发现新版本，请运行 `{_update_mod.UPGRADE_COMMAND}` 升级",
                    topic="update",
                    duration=30.0,
                )
                await asyncio.sleep(60.0)
        elif result == UpdateResult.UPDATED:
            toast("已自动更新，请重启以使用新版本", topic="update", duration=5.0)

    def _start_background_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """启动后台任务并添加到任务集合。

        Args:
            coro: 要执行的协程。

        Returns:
            创建的 asyncio.Task 对象。
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _cleanup(t: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(t)
            try:
                t.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("后台任务失败:")

        task.add_done_callback(_cleanup)
        return task

    def _cancel_background_tasks(self) -> None:
        """取消所有后台任务（通知监视器、自动更新等）。"""
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()


_NOVEL_BLUE = "dodger_blue1"
_LOGO = f"""\
[{_NOVEL_BLUE}]\
▐█▛█▛█▌
▐█████▌\
[{_NOVEL_BLUE}]\
"""


@dataclass(slots=True)
class WelcomeInfoItem:
    """欢迎信息项：表示一条欢迎信息的结构。

    Attributes:
        Level: 信息级别枚举（INFO/WARN/ERROR）。
        name: 信息名称。
        value: 信息值。
        level: 信息级别，默认为 INFO。
    """

    class Level(Enum):
        """信息级别枚举。"""

        INFO = "grey50"
        WARN = "yellow"
        ERROR = "red"

    name: str
    value: str
    level: Level = Level.INFO


def _print_welcome_info(name: str, info_items: list[WelcomeInfoItem]) -> None:
    """打印欢迎信息。

    Args:
        name: CLI 名称。
        info_items: 欢迎信息项列表。
    """
    head = Text.from_markup("欢迎来到 Novel CLI!")
    help_text = Text.from_markup("[grey50]发送 /help 获取帮助信息。[/grey50]")

    # 使用 Table 进行精确宽度控制
    logo = Text.from_markup(_LOGO)
    table = Table(show_header=False, show_edge=False, box=None, padding=(0, 1), expand=False)
    table.add_column(justify="left")
    table.add_column(justify="left")
    table.add_row(logo, Group(head, help_text))

    rows: list[RenderableType] = [table]

    if info_items:
        rows.append(Text(""))  # 空行
    for item in info_items:
        rows.append(Text(f"{item.name}: {item.value}", style=item.level.value))

    if LATEST_VERSION_FILE.exists():
        from novel_cli.constant import VERSION as current_version

        latest_version = LATEST_VERSION_FILE.read_text(encoding="utf-8").strip()
        if semver_tuple(latest_version) > semver_tuple(current_version):
            rows.append(
                Text.from_markup(
                    f"\n[yellow]发现新版本: {latest_version}。"
                    f"请运行 `{_update_mod.UPGRADE_COMMAND}` 升级。[/yellow]"
                )
            )

    console.print(
        Panel(
            Group(*rows),
            border_style=_NOVEL_BLUE,
            expand=False,
            padding=(1, 2),
        )
    )
