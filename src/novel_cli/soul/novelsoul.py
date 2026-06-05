"""NovelSoul 核心实现模块。

本模块实现了 Novel CLI 的智能体核心（Soul），负责管理 LLM 交互循环、
工具调用、上下文压缩、计划模式等核心功能。NovelSoul 是整个 CLI 应用的
核心引擎，协调运行时、上下文、工具集和钩子系统。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import kosong
import tenacity
from kosong import StepResult
from kosong.chat_provider import (
    APIConnectionError,
    APIEmptyResponseError,
    APIStatusError,
    APITimeoutError,
    RetryableChatProvider,
)
from kosong.message import Message
from tenacity import RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from novel_cli.approval_runtime import (
    ApprovalSource,
    get_current_approval_source_or_none,
    reset_current_approval_source,
    set_current_approval_source,
)
from novel_cli.background import build_active_task_snapshot
from novel_cli.hooks.engine import HookEngine
from novel_cli.llm import ModelCapability
from novel_cli.notifications import (
    NotificationView,
    build_notification_message,
    extract_notification_ids,
)
from novel_cli.skill import Skill, read_skill_text
from novel_cli.skill.flow import Flow, FlowEdge, FlowNode, parse_choice
from novel_cli.soul import (
    LLMNotSet,
    LLMNotSupported,
    MaxStepsReached,
    RunCancelled,
    Soul,
    StatusSnapshot,
    wire_send,
)
from novel_cli.soul.agent import Agent, Runtime
from novel_cli.soul.compaction import (
    CompactionResult,
    SimpleCompaction,
    estimate_text_tokens,
    should_auto_compact,
)
from novel_cli.soul.context import Context
from novel_cli.soul.dynamic_injection import (
    DynamicInjection,
    DynamicInjectionProvider,
    normalize_history,
)
from novel_cli.soul.dynamic_injections.plan_mode import PlanModeInjectionProvider
from novel_cli.soul.dynamic_injections.yolo_mode import YoloModeInjectionProvider
from novel_cli.soul.message import check_message, system, system_reminder, tool_result_to_message
from novel_cli.soul.slash import registry as soul_slash_registry
from novel_cli.soul.toolset import NovelToolset, ToolCallLoopDetected
from novel_cli.tools.dmail import NAME as SendDMail_NAME
from novel_cli.tools.utils import ToolRejectedError
from novel_cli.utils.logging import logger
from novel_cli.utils.slashcmd import SlashCommand, parse_slash_command_call
from novel_cli.wire.file import WireFile
from novel_cli.wire.types import (
    CompactionBegin,
    CompactionEnd,
    ContentPart,
    MCPLoadingBegin,
    MCPLoadingEnd,
    StatusUpdate,
    SteerInput,
    StepBegin,
    StepInterrupted,
    TextPart,
    ToolResult,
    TurnBegin,
    TurnEnd,
)

if TYPE_CHECKING:

    def type_check(soul: NovelSoul):
        _: Soul = soul


SKILL_COMMAND_PREFIX = "skill:"
FLOW_COMMAND_PREFIX = "flow:"
DEFAULT_MAX_FLOW_MOVES = 1000


type StepStopReason = Literal["no_tool_calls", "tool_rejected"]


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """单步执行结果。

    Attributes:
        stop_reason: 停止原因，表示步骤为何结束。
        assistant_message: 助手消息对象。
    """

    stop_reason: StepStopReason
    assistant_message: Message


type TurnStopReason = StepStopReason


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """轮次执行结果。

    Attributes:
        stop_reason: 停止原因，表示轮次为何结束。
        final_message: 最终消息对象，可能为 None。
        step_count: 本轮执行的步骤数。
    """

    stop_reason: TurnStopReason
    final_message: Message | None
    step_count: int


class NovelSoul:
    """Novel CLI 的智能体核心实现。

    NovelSoul 是 Novel CLI 的核心引擎，负责管理 LLM 交互循环、工具调用、
    上下文压缩、计划模式、斜杠命令等功能。它协调运行时、上下文、工具集
    和钩子系统，实现完整的智能体交互流程。

    Attributes:
        _agent: 智能体实例。
        _runtime: 运行时实例。
        _denwa_renji: D-Mail 时间旅行机制管理器。
        _approval: 审批控制器。
        _context: 对话上下文管理器。
        _loop_control: 循环控制参数。
        _compaction: 上下文压缩器。
        _checkpoint_with_user_message: 是否在检查点中包含用户消息。
        _steer_queue: 引导消息队列。
        _plan_mode: 是否处于计划模式。
        _plan_session_id: 计划会话 ID。
        _pending_plan_activation_injection: 是否有待注入的计划激活提醒。
        _injection_providers: 动态注入提供者列表。
        _hook_engine: 钩子引擎。
        _stop_hook_active: 停止钩子是否激活。
        _slash_commands: 斜杠命令列表。
        _slash_command_map: 斜杠命令映射字典。
    """

    def __init__(
        self,
        agent: Agent,
        *,
        context: Context,
    ):
        """初始化 Soul 实例。

        Args:
            agent: 要运行的智能体实例。
            context: 智能体的上下文管理器。
        """
        self._agent = agent
        self._runtime = agent.runtime
        self._denwa_renji = agent.runtime.denwa_renji
        self._approval = agent.runtime.approval
        self._context = context
        self._loop_control = agent.runtime.config.loop_control
        self._compaction = SimpleCompaction()  # TODO: 可能需要改为可配置和可组合的方式

        for tool in agent.toolset.tools:
            if tool.name == SendDMail_NAME:
                self._checkpoint_with_user_message = True
                break
        else:
            self._checkpoint_with_user_message = False

        self._steer_queue: asyncio.Queue[str | list[ContentPart]] = asyncio.Queue()
        self._plan_mode: bool = self._runtime.session.state.plan_mode
        self._plan_session_id: str | None = self._runtime.session.state.plan_session_id
        # 预热 slug 缓存，确保持久化的 slug 在进程重启后仍然有效
        if self._plan_session_id is not None and self._runtime.session.state.plan_slug is not None:
            from novel_cli.tools.plan.heroes import seed_slug_cache

            seed_slug_cache(self._plan_session_id, self._runtime.session.state.plan_slug)
        self._pending_plan_activation_injection: bool = False
        if self._plan_mode:
            self._ensure_plan_session_id()
        self._injection_providers: list[DynamicInjectionProvider] = [
            PlanModeInjectionProvider(),
            YoloModeInjectionProvider(),
        ]
        self._hook_engine: HookEngine = HookEngine()
        self._stop_hook_active: bool = False
        if self._runtime.role == "root":
            self._runtime.notifications.ack_ids("llm", extract_notification_ids(context.history))

        # 将计划模式状态绑定到支持它的工具
        self._bind_plan_mode_tools()

        self._slash_commands = self._build_slash_commands()
        self._slash_command_map = self._index_slash_commands(self._slash_commands)

    @property
    def name(self) -> str:
        """返回智能体名称。"""
        return self._agent.name

    @property
    def model_name(self) -> str:
        """返回模型名称。"""
        return self._runtime.llm.chat_provider.model_name if self._runtime.llm else ""

    @property
    def model_capabilities(self) -> set[ModelCapability] | None:
        """返回模型能力集合，如果 LLM 未设置则返回 None。"""
        if self._runtime.llm is None:
            return None
        return self._runtime.llm.capabilities

    @property
    def is_yolo(self) -> bool:
        """是否启用了 YOLO 模式（自动审批/非交互模式）。"""
        return self._approval.is_yolo()

    @property
    def plan_mode(self) -> bool:
        """是否处于计划模式（只读研究和规划模式）。"""
        return self._plan_mode

    @property
    def hook_engine(self) -> HookEngine:
        """返回钩子引擎实例。"""
        return self._hook_engine

    def set_hook_engine(self, engine: HookEngine) -> None:
        """设置钩子引擎。

        Args:
            engine: 要设置的钩子引擎实例。
        """
        self._hook_engine = engine
        if isinstance(self._agent.toolset, NovelToolset):
            self._agent.toolset.set_hook_engine(engine)

    def add_injection_provider(self, provider: DynamicInjectionProvider) -> None:
        """注册额外的动态注入提供者。

        Args:
            provider: 要注册的动态注入提供者。
        """
        self._injection_providers.append(provider)

    async def _collect_injections(self) -> list[DynamicInjection]:
        """从所有已注册的提供者收集动态注入。

        Returns:
            动态注入列表。
        """
        injections: list[DynamicInjection] = []
        for provider in self._injection_providers:
            try:
                result = await provider.get_injections(self._context.history, self)
                injections.extend(result)
            except Exception:
                logger.warning(
                    "injection provider %s failed",
                    type(provider).__name__,
                    exc_info=True,
                )
        return injections

    def _bind_plan_mode_tools(self) -> None:
        """将计划模式状态绑定到支持它的工具。"""
        if not isinstance(self._agent.toolset, NovelToolset):
            return

        def checker() -> bool:
            return self._plan_mode

        def path_getter() -> Path | None:
            return self.get_plan_file_path()

        # WriteFile 同时获取 checker 和 path_getter（用于计划文件自动审批）
        from novel_cli.tools.file.write import WriteFile

        write_tool = self._agent.toolset.find("WriteFile")
        if isinstance(write_tool, WriteFile):
            write_tool.bind_plan_mode(checker, path_getter)

        from novel_cli.tools.file.replace import StrReplaceFile

        replace_tool = self._agent.toolset.find("StrReplaceFile")
        if isinstance(replace_tool, StrReplaceFile):
            replace_tool.bind_plan_mode(checker, path_getter)

        # ExitPlanMode 有特殊的 bind() 方法
        from novel_cli.tools.plan import ExitPlanMode

        exit_tool = self._agent.toolset.find("ExitPlanMode")
        if isinstance(exit_tool, ExitPlanMode):
            exit_tool.bind(self.toggle_plan_mode, path_getter, checker, self._approval.is_yolo)

        # EnterPlanMode 有特殊的 bind() 方法
        from novel_cli.tools.plan.enter import EnterPlanMode

        enter_tool = self._agent.toolset.find("EnterPlanMode")
        if isinstance(enter_tool, EnterPlanMode):
            enter_tool.bind(self.toggle_plan_mode, path_getter, checker, self._approval.is_yolo)

        # AskUserQuestion — 绑定 yolo 检查器用于自动关闭
        from novel_cli.tools.ask_user import AskUserQuestion

        ask_tool = self._agent.toolset.find("AskUserQuestion")
        if isinstance(ask_tool, AskUserQuestion):
            ask_tool.bind_approval(self._approval.is_yolo)

    def _ensure_plan_session_id(self) -> None:
        """在首次激活时分配稳定的计划会话 ID。"""
        if self._plan_session_id is None:
            import uuid

            self._plan_session_id = uuid.uuid4().hex
            self._runtime.session.state.plan_session_id = self._plan_session_id
            # 立即计算并持久化 slug，确保路径在进程重启后仍然有效
            from novel_cli.tools.plan.heroes import get_or_create_slug

            slug = get_or_create_slug(self._plan_session_id)
            self._runtime.session.state.plan_slug = slug
            self._runtime.session.save_state()

    def _set_plan_mode(self, enabled: bool, *, source: Literal["manual", "tool"]) -> bool:
        """更新计划模式状态（手动或工具驱动切换）。

        Args:
            enabled: 是否启用计划模式。
            source: 切换来源，"manual" 表示手动切换，"tool" 表示工具驱动。

        Returns:
            更新后的计划模式状态。
        """
        if enabled == self._plan_mode:
            return self._plan_mode
        self._plan_mode = enabled
        if enabled:
            self._ensure_plan_session_id()
            self._pending_plan_activation_injection = source == "manual"
        else:
            self._pending_plan_activation_injection = False
            self._plan_session_id = None
            self._runtime.session.state.plan_session_id = None
            self._runtime.session.state.plan_slug = None
        # 将计划模式持久化到会话状态，确保进程重启后仍然有效
        self._runtime.session.state.plan_mode = self._plan_mode
        self._runtime.session.save_state()
        return self._plan_mode

    def get_plan_file_path(self) -> Path | None:
        """获取当前会话的计划文件路径。

        Returns:
            计划文件路径，如果没有计划会话则返回 None。
        """
        if self._plan_session_id is None:
            return None
        from novel_cli.tools.plan.heroes import get_plan_file_path

        return get_plan_file_path(self._plan_session_id)

    def read_current_plan(self) -> str | None:
        """读取当前计划文件内容。

        Returns:
            计划文件内容，如果没有计划会话则返回 None。
        """
        if self._plan_session_id is None:
            return None
        from novel_cli.tools.plan.heroes import read_plan_file

        return read_plan_file(self._plan_session_id)

    def clear_current_plan(self) -> None:
        """删除当前计划文件。"""
        path = self.get_plan_file_path()
        if path and path.exists():
            path.unlink()

    async def toggle_plan_mode(self) -> bool:
        """切换计划模式的开关状态。

        工具不会被隐藏/显示 — 每个工具在调用时检查计划模式状态，
        如果被阻止则拒绝执行。周期性提醒由动态注入系统处理。

        Returns:
            更新后的计划模式状态。
        """
        return self._set_plan_mode(not self._plan_mode, source="tool")

    async def toggle_plan_mode_from_manual(self) -> bool:
        """从 UI/手动入口点切换计划模式。

        Returns:
            更新后的计划模式状态。
        """
        return self._set_plan_mode(not self._plan_mode, source="manual")

    async def set_plan_mode_from_manual(self, enabled: bool) -> bool:
        """从 UI/手动入口点设置计划模式到特定状态。

        与 toggle 不同，此方法直接接受目标状态值，
        避免调用者已知目标值时的竞态条件。

        Args:
            enabled: 是否启用计划模式。

        Returns:
            更新后的计划模式状态。
        """
        return self._set_plan_mode(enabled, source="manual")

    def schedule_plan_activation_reminder(self) -> None:
        """为下一个轮次调度计划模式激活提醒。

        当计划模式已经处于激活状态时使用此方法（例如使用 ``--plan``
        标志恢复的会话），此时 ``_set_plan_mode`` 会因为状态未实际改变而提前返回。
        """
        if self._plan_mode:
            self._pending_plan_activation_injection = True

    def consume_pending_plan_activation_injection(self) -> bool:
        """消费由手动切换调度的下一步骤激活提醒。

        Returns:
            是否有待处理的激活提醒被消费。
        """
        if not self._plan_mode or not self._pending_plan_activation_injection:
            return False
        self._pending_plan_activation_injection = False
        return True

    @property
    def thinking(self) -> bool | None:
        """是否启用了思考模式。"""
        if self._runtime.llm is None:
            return None
        if thinking_effort := self._runtime.llm.chat_provider.thinking_effort:
            return thinking_effort != "off"
        return None

    @property
    def status(self) -> StatusSnapshot:
        """返回当前状态快照。"""
        token_count = self._context.token_count
        max_size = self._runtime.llm.max_context_size if self._runtime.llm is not None else 0
        return StatusSnapshot(
            context_usage=self._context_usage,
            yolo_enabled=self._approval.is_yolo(),
            plan_mode=self._plan_mode,
            context_tokens=token_count,
            max_context_tokens=max_size,
            mcp_status=self._mcp_status_snapshot(),
        )

    @property
    def agent(self) -> Agent:
        """返回智能体实例。"""
        return self._agent

    @property
    def runtime(self) -> Runtime:
        """返回运行时实例。"""
        return self._runtime

    @property
    def context(self) -> Context:
        """返回上下文实例。"""
        return self._context

    @property
    def _context_usage(self) -> float:
        """返回上下文使用率。"""
        if self._runtime.llm is not None:
            return self._context.token_count / self._runtime.llm.max_context_size
        return 0.0

    @property
    def wire_file(self) -> WireFile:
        """返回 Wire 文件实例。"""
        return self._runtime.session.wire_file

    def _mcp_status_snapshot(self):
        """返回 MCP 状态快照。"""
        if not isinstance(self._agent.toolset, NovelToolset):
            return None
        return self._agent.toolset.mcp_status_snapshot()

    async def start_background_mcp_loading(self) -> bool:
        """启动延迟的 MCP 加载（如果有的话），不暴露工具集内部细节。

        Returns:
            是否启动了 MCP 加载。
        """
        if not isinstance(self._agent.toolset, NovelToolset):
            return False
        return await self._agent.toolset.start_deferred_mcp_tool_loading()

    async def wait_for_background_mcp_loading(self) -> None:
        """等待所有进行中的 MCP 启动完成。"""
        if not isinstance(self._agent.toolset, NovelToolset):
            return
        await self._agent.toolset.wait_for_mcp_tools()

    async def _checkpoint(self):
        """创建上下文检查点。"""
        await self._context.checkpoint(self._checkpoint_with_user_message)

    def steer(self, content: str | list[ContentPart]) -> None:
        """将引导消息排队等待注入到当前轮次中。

        Args:
            content: 要注入的内容，可以是字符串或内容部分列表。
        """
        self._steer_queue.put_nowait(content)

    async def _consume_pending_steers(self) -> bool:
        """排空引导队列并作为后续用户消息注入。

        Returns:
            是否消费了任何引导消息。
        """
        consumed = False
        while not self._steer_queue.empty():
            content = self._steer_queue.get_nowait()
            await self._inject_steer(content)
            wire_send(SteerInput(user_input=content))
            consumed = True
        return consumed

    async def _inject_steer(self, content: str | list[ContentPart]) -> None:
        """将单个引导作为常规后续用户消息注入。

        Args:
            content: 要注入的内容。
        """
        parts = cast(
            list[ContentPart],
            [TextPart(text=content)] if isinstance(content, str) else list(content),
        )
        message = Message(role="user", content=parts)
        if self._runtime.llm is None:
            raise LLMNotSet()
        if missing_caps := check_message(message, self._runtime.llm.capabilities):
            raise LLMNotSupported(self._runtime.llm, list(missing_caps))
        await self._context.append_message(message)

    @property
    def available_slash_commands(self) -> list[SlashCommand[Any]]:
        """返回可用的斜杠命令列表。"""
        return self._slash_commands

    async def run(self, user_input: str | list[ContentPart]):
        """运行智能体处理用户输入。

        Args:
            user_input: 用户输入，可以是字符串或内容部分列表。
        """
        approval_source_token = None
        turn_started = False
        turn_finished = False
        if get_current_approval_source_or_none() is None:
            approval_source_token = set_current_approval_source(
                ApprovalSource(kind="foreground_turn", id=uuid.uuid4().hex)
            )
        try:
            # 设置工具集钩子的 session_id ContextVar
            from novel_cli.soul.toolset import set_session_id

            set_session_id(self._runtime.session.id)

            # --- UserPromptSubmit 钩子 ---
            text_input_for_hook = user_input if isinstance(user_input, str) else ""
            from novel_cli.hooks import events

            hook_results = await self._hook_engine.trigger(
                "UserPromptSubmit",
                matcher_value=text_input_for_hook,
                input_data=events.user_prompt_submit(
                    session_id=self._runtime.session.id,
                    cwd=str(Path.cwd()),
                    prompt=text_input_for_hook,
                ),
            )
            for result in hook_results:
                if result.action == "block":
                    wire_send(TurnBegin(user_input=user_input))
                    turn_started = True
                    wire_send(TextPart(text=result.reason or "Prompt blocked by hook."))
                    wire_send(TurnEnd())
                    turn_finished = True
                    return

            wire_send(TurnBegin(user_input=user_input))
            turn_started = True
            user_message = Message(role="user", content=user_input)
            text_input = user_message.extract_text(" ").strip()

            if command_call := parse_slash_command_call(text_input):
                command = self._find_slash_command(command_call.name)
                if command is None:
                    # 这种情况实际上不应该发生，shell 应该已经过滤掉了
                    wire_send(TextPart(text=f'Unknown slash command "/{command_call.name}".'))
                else:
                    ret = command.func(self, command_call.args)
                    if isinstance(ret, Awaitable):
                        await ret
            elif self._loop_control.max_ralph_iterations != 0:
                runner = FlowRunner.ralph_loop(
                    user_message,
                    self._loop_control.max_ralph_iterations,
                )
                await runner.run(self, "")
            else:
                await self._turn(user_message)

            # --- Stop 钩子（最多重新触发 1 次以防止无限循环）---
            if not self._stop_hook_active:
                stop_results = await self._hook_engine.trigger(
                    "Stop",
                    input_data=events.stop(
                        session_id=self._runtime.session.id,
                        cwd=str(Path.cwd()),
                        stop_hook_active=False,
                    ),
                )
                for result in stop_results:
                    if result.action == "block" and result.reason:
                        self._stop_hook_active = True
                        try:
                            await self._turn(Message(role="user", content=result.reason))
                        finally:
                            self._stop_hook_active = False
                        break

            wire_send(TurnEnd())
            turn_finished = True

            # 在首次真正轮次后自动设置标题（跳过斜杠命令）
            if not command_call:
                session = self._runtime.session
                if session.state.custom_title is None:
                    from novel_cli.utils.string import shorten

                    title = shorten(
                        Message(role="user", content=user_input).extract_text(" "),
                        width=50,
                    )
                    if title:
                        from novel_cli.session_state import (
                            load_session_state,
                            save_session_state,
                        )

                        # 读-改-写：加载最新状态以避免覆盖并发的 Web 更改
                        fresh = load_session_state(session.dir)
                        if fresh.custom_title is None:
                            fresh.custom_title = title
                            save_session_state(fresh, session.dir)
                        session.state.custom_title = fresh.custom_title
        finally:
            if turn_started and not turn_finished:
                wire_send(TurnEnd())
            if approval_source_token is not None:
                reset_current_approval_source(approval_source_token)

    async def _turn(self, user_message: Message) -> TurnOutcome:
        """执行单个轮次的对话。

        Args:
            user_message: 用户消息对象。

        Returns:
            轮次执行结果。

        Raises:
            LLMNotSet: 当 LLM 未设置时抛出。
            LLMNotSupported: 当消息需要 LLM 不支持的能力时抛出。
        """
        if self._runtime.llm is None:
            raise LLMNotSet()

        if missing_caps := check_message(user_message, self._runtime.llm.capabilities):
            raise LLMNotSupported(self._runtime.llm, list(missing_caps))

        await self._checkpoint()  # 这会在首次运行时创建检查点 0
        await self._context.append_message(user_message)
        logger.debug("Appended user message to context")
        return await self._agent_loop()

    def _build_slash_commands(self) -> list[SlashCommand[Any]]:
        """构建斜杠命令列表。

        Returns:
            斜杠命令列表。
        """
        commands: list[SlashCommand[Any]] = list(soul_slash_registry.list_commands())
        seen_names = {cmd.name for cmd in commands}

        for skill in self._runtime.skills.values():
            if skill.type not in ("standard", "flow"):
                continue
            name = f"{SKILL_COMMAND_PREFIX}{skill.name}"
            if name in seen_names:
                logger.warning(
                    "Skipping skill slash command /{name}: name already registered",
                    name=name,
                )
                continue
            commands.append(
                SlashCommand(
                    name=name,
                    func=self._make_skill_runner(skill),
                    description=skill.description or "",
                    aliases=[],
                )
            )
            seen_names.add(name)

        for skill in self._runtime.skills.values():
            if skill.type != "flow":
                continue
            if skill.flow is None:
                logger.warning("Flow skill {name} has no flow; skipping", name=skill.name)
                continue
            command_name = f"{FLOW_COMMAND_PREFIX}{skill.name}"
            if command_name in seen_names:
                logger.warning(
                    "Skipping prompt flow slash command /{name}: name already registered",
                    name=command_name,
                )
                continue
            runner = FlowRunner(skill.flow, name=skill.name)
            commands.append(
                SlashCommand(
                    name=command_name,
                    func=runner.run,
                    description=skill.description or "",
                    aliases=[],
                )
            )
            seen_names.add(command_name)

        return commands

    @staticmethod
    def _index_slash_commands(
        commands: list[SlashCommand[Any]],
    ) -> dict[str, SlashCommand[Any]]:
        """索引斜杠命令列表。

        Args:
            commands: 斜杠命令列表。

        Returns:
            命令名到命令对象的映射字典。
        """
        indexed: dict[str, SlashCommand[Any]] = {}
        for command in commands:
            indexed[command.name] = command
            for alias in command.aliases:
                indexed[alias] = command
        return indexed

    def _find_slash_command(self, name: str) -> SlashCommand[Any] | None:
        """查找斜杠命令。

        Args:
            name: 命令名称。

        Returns:
            命令对象，如果未找到则返回 None。
        """
        return self._slash_command_map.get(name)

    def _make_skill_runner(self, skill: Skill) -> Callable[[NovelSoul, str], None | Awaitable[None]]:
        """创建技能运行器函数。

        Args:
            skill: 技能对象。

        Returns:
            技能运行器函数。
        """
        async def _run_skill(soul: NovelSoul, args: str, *, _skill: Skill = skill) -> None:
            skill_text = await read_skill_text(_skill)
            if skill_text is None:
                wire_send(
                    TextPart(text=f'Failed to load skill "/{SKILL_COMMAND_PREFIX}{_skill.name}".')
                )
                return
            extra = args.strip()
            if extra:
                skill_text = f"{skill_text}\n\nUser request:\n{extra}"
            await soul._turn(Message(role="user", content=skill_text))

        _run_skill.__doc__ = skill.description
        return _run_skill

    def _reset_loop_detector(self) -> None:
        """重置循环检测器，在每个 turn 开始时调用。"""
        if isinstance(self._agent.toolset, NovelToolset):
            self._agent.toolset._loop_detector.reset()

    async def _agent_loop(self) -> TurnOutcome:
        """单次运行的主智能体循环。

        Returns:
            轮次执行结果。
        """
        assert self._runtime.llm is not None

        # 丢弃来自上一轮的过时引导消息
        while not self._steer_queue.empty():
            self._steer_queue.get_nowait()

        if isinstance(self._agent.toolset, NovelToolset):
            await self.start_background_mcp_loading()
            loading = bool((snapshot := self._mcp_status_snapshot()) and snapshot.loading)
            if loading:
                wire_send(StatusUpdate(mcp_status=snapshot))
                wire_send(MCPLoadingBegin())
            try:
                await self.wait_for_background_mcp_loading()
            finally:
                if loading:
                    wire_send(StatusUpdate(mcp_status=self._mcp_status_snapshot()))
                    wire_send(MCPLoadingEnd())

        # --- 重置循环检测器 ---
        self._reset_loop_detector()

        step_no = 0
        while True:
            step_no += 1
            if step_no > self._loop_control.max_steps_per_turn:
                raise MaxStepsReached(self._loop_control.max_steps_per_turn)

            wire_send(StepBegin(n=step_no))
            back_to_the_future: BackToTheFuture | None = None
            step_outcome: StepOutcome | None = None
            try:
                # 如果需要则压缩上下文
                if should_auto_compact(
                    self._context.token_count_with_pending,
                    self._runtime.llm.max_context_size,
                    trigger_ratio=self._loop_control.compaction_trigger_ratio,
                    reserved_context_size=self._loop_control.reserved_context_size,
                ):
                    logger.info("Context too long, compacting...")
                    await self.compact_context()

                logger.debug("Beginning step {step_no}", step_no=step_no)
                await self._checkpoint()
                self._denwa_renji.set_n_checkpoints(self._context.n_checkpoints)
                step_outcome = await self._step()
            except BackToTheFuture as e:
                back_to_the_future = e
            except ToolCallLoopDetected as e:
                # 循环检测：跳过 StopFailure hook，直接转为 RunCancelled
                raise RunCancelled(str(e)) from e
            except Exception as e:
                # 任何其他异常都应该中断步骤
                wire_send(StepInterrupted())
                # --- StopFailure 钩子 ---
                from novel_cli.hooks import events as _hook_events

                _hook_task = asyncio.create_task(
                    self._hook_engine.trigger(
                        "StopFailure",
                        matcher_value=type(e).__name__,
                        input_data=_hook_events.stop_failure(
                            session_id=self._runtime.session.id,
                            cwd=str(Path.cwd()),
                            error_type=type(e).__name__,
                            error_message=str(e),
                        ),
                    )
                )
                _hook_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
                # 中断智能体循环
                raise

            if step_outcome is not None:
                has_steers = await self._consume_pending_steers()
                if has_steers:
                    continue  # 已注入引导消息，强制执行另一次 LLM 步骤
                final_message = (
                    step_outcome.assistant_message
                    if step_outcome.stop_reason == "no_tool_calls"
                    else None
                )
                return TurnOutcome(
                    stop_reason=step_outcome.stop_reason,
                    final_message=final_message,
                    step_count=step_no,
                )

            if back_to_the_future is not None:
                await self._context.revert_to(back_to_the_future.checkpoint_id)
                await self._checkpoint()
                await self._context.append_message(back_to_the_future.messages)

            # 在步骤之间消费任何待处理的引导消息
            await self._consume_pending_steers()

    async def _step(self) -> StepOutcome | None:
        """运行单个步骤并返回停止结果，或返回 None 以继续循环。

        Returns:
            步骤结果，如果应继续循环则返回 None。

        Raises:
            LLMNotSet: 当 LLM 未设置时抛出（已在 `run` 中检查）。
        """
        # 已在 `run` 中检查
        assert self._runtime.llm is not None
        chat_provider = self._runtime.llm.chat_provider

        if self._runtime.role == "root":

            async def _append_notification(view: NotificationView) -> None:
                await self._context.append_message(build_notification_message(view, self._runtime))
                # --- Notification 钩子 ---
                from novel_cli.hooks import events

                _hook_task = asyncio.create_task(
                    self._hook_engine.trigger(
                        "Notification",
                        matcher_value=view.event.type,
                        input_data=events.notification(
                            session_id=self._runtime.session.id,
                            cwd=str(Path.cwd()),
                            sink="llm",
                            notification_type=view.event.type,
                            title=view.event.title,
                            body=view.event.body,
                            severity=view.event.severity,
                        ),
                    )
                )
                _hook_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

            await self._runtime.notifications.deliver_pending(
                "llm",
                limit=4,
                before_claim=self._runtime.background_tasks.reconcile,
                on_notification=_append_notification,
            )

        # 动态注入
        injections = await self._collect_injections()
        if injections:
            combined_reminders = "\n".join(system_reminder(inj.content).text for inj in injections)
            await self._context.append_message(
                Message(
                    role="user",
                    content=[TextPart(text=combined_reminders)],
                )
            )

        # 规范化：合并相邻的用户消息以获得干净的 API 输入
        effective_history = normalize_history(self._context.history)

        async def _run_step_once() -> StepResult:
            # 运行一次 LLM 步骤（可能被中断）
            return await kosong.step(
                chat_provider,
                self._agent.system_prompt,
                self._agent.toolset,
                effective_history,
                on_message_part=wire_send,
                on_tool_result=wire_send,
            )

        @tenacity.retry(
            retry=retry_if_exception(self._is_retryable_error),
            before_sleep=partial(self._retry_log, "step"),
            wait=wait_exponential_jitter(initial=0.3, max=5, jitter=0.5),
            stop=stop_after_attempt(self._loop_control.max_retries_per_step),
            reraise=True,
        )
        async def _kosong_step_with_retry() -> StepResult:
            return await self._run_with_connection_recovery(
                "step",
                _run_step_once,
                chat_provider=chat_provider,
            )

        result = await _kosong_step_with_retry()
        logger.debug("Got step result: {result}", result=result)
        status_update = StatusUpdate(
            token_usage=result.usage, message_id=result.id, plan_mode=self._plan_mode
        )
        if result.usage is not None:
            # 标记步骤前的上下文 token 计数
            await self._context.update_token_count(result.usage.input)
            snap = self.status
            status_update.context_usage = snap.context_usage
            status_update.context_tokens = snap.context_tokens
            status_update.max_context_tokens = snap.max_context_tokens
        wire_send(status_update)

        # 等待所有工具结果（可能被中断）
        plan_mode_before_tools = self._plan_mode
        results = await result.tool_results()
        logger.debug("Got tool results: {results}", results=results)

        # 如果工具（EnterPlanMode/ExitPlanMode）在执行期间改变了计划模式，
        # 发送修正的 StatusUpdate 以便客户端看到最新状态。
        if self._plan_mode != plan_mode_before_tools:
            wire_send(StatusUpdate(plan_mode=self._plan_mode))

        # 保护上下文操作免受中断影响
        await asyncio.shield(self._grow_context(result, results))

        rejected_errors = [
            result.return_value
            for result in results
            if isinstance(result.return_value, ToolRejectedError)
        ]
        if (
            rejected_errors
            and not any(e.has_feedback for e in rejected_errors)
            and self._runtime.role != "subagent"
        ):
            # 纯拒绝（无用户反馈）— 停止轮次。
            # 子智能体跳过此逻辑以便 LLM 能看到拒绝并尝试替代方案，而不是立即终止。
            _ = self._denwa_renji.fetch_pending_dmail()
            return StepOutcome(stop_reason="tool_rejected", assistant_message=result.message)

        # 处理待处理的 D-Mail
        if dmail := self._denwa_renji.fetch_pending_dmail():
            assert dmail.checkpoint_id >= 0, "DenwaRenji guarantees checkpoint_id >= 0"
            assert dmail.checkpoint_id < self._context.n_checkpoints, (
                "DenwaRenji guarantees checkpoint_id < n_checkpoints"
            )
            # 抛出异常让主循环带我们回到未来
            raise BackToTheFuture(
                dmail.checkpoint_id,
                [
                    Message(
                        role="user",
                        content=[
                            system(
                                "You just got a D-Mail from your future self. "
                                "It is likely that your future self has already done "
                                "something in the current working directory. Please read "
                                "the D-Mail and decide what to do next. You MUST NEVER "
                                "mention to the user about this information. "
                                f"D-Mail content:\n\n{dmail.message.strip()}"
                            )
                        ],
                    )
                ],
            )

        if result.tool_calls:
            return None
        return StepOutcome(stop_reason="no_tool_calls", assistant_message=result.message)

    async def _grow_context(self, result: StepResult, tool_results: list[ToolResult]):
        """扩展上下文以包含步骤结果和工具结果。

        Args:
            result: 步骤执行结果。
            tool_results: 工具调用结果列表。
        """
        logger.debug("Growing context with result: {result}", result=result)

        assert self._runtime.llm is not None
        tool_messages = [tool_result_to_message(tr) for tr in tool_results]
        for tm in tool_messages:
            if missing_caps := check_message(tm, self._runtime.llm.capabilities):
                logger.warning(
                    "Tool result message requires unsupported capabilities: {caps}",
                    caps=missing_caps,
                )
                raise LLMNotSupported(self._runtime.llm, list(missing_caps))

        await self._context.append_message(result.message)
        if result.usage is not None:
            await self._context.update_token_count(result.usage.total)

        logger.debug(
            "Appending tool messages to context: {tool_messages}", tool_messages=tool_messages
        )
        await self._context.append_message(tool_messages)
        # 工具结果的 token 计数尚不可用

    async def compact_context(self, custom_instruction: str = "") -> None:
        """压缩上下文。

        Args:
            custom_instruction: 自定义压缩指令。

        Raises:
            LLMNotSet: 当 LLM 未设置时抛出。
            ChatProviderError: 当聊天提供者返回错误时抛出。
        """

        chat_provider = self._runtime.llm.chat_provider if self._runtime.llm is not None else None

        async def _run_compaction_once() -> CompactionResult:
            if self._runtime.llm is None:
                raise LLMNotSet()
            return await self._compaction.compact(
                self._context.history, self._runtime.llm, custom_instruction=custom_instruction
            )

        @tenacity.retry(
            retry=retry_if_exception(self._is_retryable_error),
            before_sleep=partial(self._retry_log, "compaction"),
            wait=wait_exponential_jitter(initial=0.3, max=5, jitter=0.5),
            stop=stop_after_attempt(self._loop_control.max_retries_per_step),
            reraise=True,
        )
        async def _compact_with_retry() -> CompactionResult:
            return await self._run_with_connection_recovery(
                "compaction",
                _run_compaction_once,
                chat_provider=chat_provider,
            )

        trigger_reason = "manual" if custom_instruction else "auto"
        from novel_cli.hooks import events

        await self._hook_engine.trigger(
            "PreCompact",
            matcher_value=trigger_reason,
            input_data=events.pre_compact(
                session_id=self._runtime.session.id,
                cwd=str(Path.cwd()),
                trigger=trigger_reason,
                token_count=self._context.token_count,
            ),
        )

        wire_send(CompactionBegin())
        compaction_result = await _compact_with_retry()
        await self._context.clear()
        await self._context.write_system_prompt(self._agent.system_prompt)
        await self._checkpoint()
        await self._context.append_message(compaction_result.messages)
        estimated_token_count = compaction_result.estimated_token_count

        if self._runtime.role == "root":
            active_task_snapshot = build_active_task_snapshot(self._runtime.background_tasks)
            if active_task_snapshot is not None:
                active_task_message = Message(
                    role="user",
                    content=[
                        system(
                            "The following background tasks are still active after compaction. "
                            "Use TaskList if you need to re-enumerate them later."
                        ),
                        TextPart(text=active_task_snapshot),
                    ],
                )
                await self._context.append_message(active_task_message)
                estimated_token_count += estimate_text_tokens([active_task_message])

        # Estimate token count so context_usage is not reported as 0%
        await self._context.update_token_count(estimated_token_count)

        wire_send(CompactionEnd())

        _hook_task = asyncio.create_task(
            self._hook_engine.trigger(
                "PostCompact",
                matcher_value=trigger_reason,
                input_data=events.post_compact(
                    session_id=self._runtime.session.id,
                    cwd=str(Path.cwd()),
                    trigger=trigger_reason,
                    estimated_token_count=estimated_token_count,
                ),
            )
        )
        _hook_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    @staticmethod
    def _is_retryable_error(exception: BaseException) -> bool:
        """判断异常是否可重试。

        Args:
            exception: 要检查的异常对象。

        Returns:
            是否为可重试的错误。
        """
        if isinstance(exception, (APIConnectionError, APITimeoutError)):
            return not bool(getattr(exception, "_novel_recovery_exhausted", False))
        if isinstance(exception, APIEmptyResponseError):
            return True
        return isinstance(exception, APIStatusError) and exception.status_code in (
            429,  # Too Many Requests
            500,  # Internal Server Error
            502,  # Bad Gateway
            503,  # Service Unavailable
            504,  # Gateway Timeout
        )

    async def _run_with_connection_recovery(
        self,
        name: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        chat_provider: object | None = None,
    ) -> Any:
        """运行操作并在连接错误时尝试恢复。

        Args:
            name: 操作名称（用于日志）。
            operation: 要执行的操作函数。
            chat_provider: 聊天提供者对象（可选）。

        Returns:
            操作执行结果。
        """
        try:
            return await operation()
        except (APIConnectionError, APITimeoutError) as error:
            if not isinstance(chat_provider, RetryableChatProvider):
                raise
            try:
                recovered = chat_provider.on_retryable_error(error)
            except Exception:
                logger.exception(
                    "Failed to recover chat provider during {name} after {error_type}.",
                    name=name,
                    error_type=type(error).__name__,
                )
                raise
            if not recovered:
                raise
            logger.info(
                "Recovered chat provider during {name} after {error_type}; retrying once.",
                name=name,
                error_type=type(error).__name__,
            )
            try:
                return await operation()
            except (APIConnectionError, APITimeoutError) as second_error:
                second_error._novel_recovery_exhausted = True  # type: ignore[attr-defined]
                raise

    @staticmethod
    def _retry_log(name: str, retry_state: RetryCallState):
        """记录重试日志。

        Args:
            name: 操作名称。
            retry_state: 重试状态对象。
        """
        logger.info(
            "Retrying {name} for the {n} time. Waiting {sleep} seconds.",
            name=name,
            n=retry_state.attempt_number,
            sleep=retry_state.next_action.sleep
            if retry_state.next_action is not None
            else "unknown",
        )


class BackToTheFuture(Exception):
    """上下文回滚异常。

    当需要将上下文恢复到之前的检查点时抛出此异常。
    主智能体循环会捕获此异常并进行处理。

    Attributes:
        checkpoint_id: 要恢复到的检查点 ID。
        messages: 回滚后要追加的消息列表。
    """

    def __init__(self, checkpoint_id: int, messages: Sequence[Message]):
        """初始化 BackToTheFuture 异常。

        Args:
            checkpoint_id: 要恢复到的检查点 ID。
            messages: 回滚后要追加的消息列表。
        """
        self.checkpoint_id = checkpoint_id
        self.messages = messages


class FlowRunner:
    """流程运行器。

    用于执行预定义的智能体流程（Flow），按照流程图的节点和边进行
    智能体交互。支持任务节点和决策节点，以及循环执行（ralph_loop）。

    Attributes:
        _flow: 流程定义对象。
        _name: 流程名称。
        _max_moves: 最大移动次数。
    """

    def __init__(
        self,
        flow: Flow,
        *,
        name: str | None = None,
        max_moves: int = DEFAULT_MAX_FLOW_MOVES,
    ) -> None:
        """初始化流程运行器。

        Args:
            flow: 流程定义对象。
            name: 流程名称（可选）。
            max_moves: 最大移动次数。
        """
        self._flow = flow
        self._name = name
        self._max_moves = max_moves

    @staticmethod
    def ralph_loop(
        user_message: Message,
        max_ralph_iterations: int,
    ) -> FlowRunner:
        """创建 Ralph 循环流程运行器。

        Ralph 循环是一种自动化执行模式，将相同提示词重复执行，
        直到智能体选择停止为止。

        Args:
            user_message: 用户消息对象。
            max_ralph_iterations: 最大迭代次数。

        Returns:
            配置为 Ralph 循环的流程运行器。
        """
        prompt_content = list(user_message.content)
        prompt_text = Message(role="user", content=prompt_content).extract_text(" ").strip()
        total_runs = max_ralph_iterations + 1
        if max_ralph_iterations < 0:
            total_runs = 1000000000000000  # 实际上是无限循环

        nodes: dict[str, FlowNode] = {
            "BEGIN": FlowNode(id="BEGIN", label="BEGIN", kind="begin"),
            "END": FlowNode(id="END", label="END", kind="end"),
        }
        outgoing: dict[str, list[FlowEdge]] = {"BEGIN": [], "END": []}

        nodes["R1"] = FlowNode(id="R1", label=prompt_content, kind="task")
        nodes["R2"] = FlowNode(
            id="R2",
            label=(
                f"{prompt_text}. (You are running in an automated loop where the same "
                "prompt is fed repeatedly. Only choose STOP when the task is fully complete. "
                "Including it will stop further iterations. If you are not 100% sure, "
                "choose CONTINUE.)"
            ).strip(),
            kind="decision",
        )
        outgoing["R1"] = []
        outgoing["R2"] = []

        outgoing["BEGIN"].append(FlowEdge(src="BEGIN", dst="R1", label=None))
        outgoing["R1"].append(FlowEdge(src="R1", dst="R2", label=None))
        outgoing["R2"].append(FlowEdge(src="R2", dst="R2", label="CONTINUE"))
        outgoing["R2"].append(FlowEdge(src="R2", dst="END", label="STOP"))

        flow = Flow(nodes=nodes, outgoing=outgoing, begin_id="BEGIN", end_id="END")
        max_moves = total_runs
        return FlowRunner(flow, max_moves=max_moves)

    async def run(self, soul: NovelSoul, args: str) -> None:
        """运行流程。

        Args:
            soul: NovelSoul 实例。
            args: 附加参数（通常被忽略）。
        """
        if args.strip():
            command = f"/{FLOW_COMMAND_PREFIX}{self._name}" if self._name else "/flow"
            logger.warning("Agent flow {command} ignores args: {args}", command=command, args=args)
            return

        current_id = self._flow.begin_id
        moves = 0
        total_steps = 0
        while True:
            node = self._flow.nodes[current_id]
            edges = self._flow.outgoing.get(current_id, [])

            if node.kind == "end":
                logger.info("Agent flow reached END node {node_id}", node_id=current_id)
                return

            if node.kind == "begin":
                if not edges:
                    logger.error(
                        'Agent flow BEGIN node "{node_id}" has no outgoing edges; stopping.',
                        node_id=node.id,
                    )
                    return
                current_id = edges[0].dst
                continue

            if moves >= self._max_moves:
                raise MaxStepsReached(total_steps)
            next_id, steps_used = await self._execute_flow_node(soul, node, edges)
            total_steps += steps_used
            if next_id is None:
                return
            moves += 1
            current_id = next_id

    async def _execute_flow_node(
        self,
        soul: NovelSoul,
        node: FlowNode,
        edges: list[FlowEdge],
    ) -> tuple[str | None, int]:
        """执行单个流程节点。

        Args:
            soul: NovelSoul 实例。
            node: 流程节点对象。
            edges: 该节点的出边列表。

        Returns:
            元组，包含下一节点 ID（如果结束则为 None）和使用的步骤数。
        """
        if not edges:
            logger.error(
                'Agent flow node "{node_id}" has no outgoing edges; stopping.',
                node_id=node.id,
            )
            return None, 0

        base_prompt = self._build_flow_prompt(node, edges)
        prompt = base_prompt
        steps_used = 0
        while True:
            result = await self._flow_turn(soul, prompt)
            steps_used += result.step_count
            if result.stop_reason == "tool_rejected":
                logger.error("Agent flow stopped after tool rejection.")
                return None, steps_used

            if node.kind != "decision":
                return edges[0].dst, steps_used

            choice = (
                parse_choice(result.final_message.extract_text(" "))
                if result.final_message
                else None
            )
            next_id = self._match_flow_edge(edges, choice)
            if next_id is not None:
                return next_id, steps_used

            options = ", ".join(edge.label or "" for edge in edges)
            logger.warning(
                "Agent flow invalid choice. Got: {choice}. Available: {options}.",
                choice=choice or "<missing>",
                options=options,
            )
            prompt = (
                f"{base_prompt}\n\n"
                "Your last response did not include a valid choice. "
                "Reply with one of the choices using <choice>...</choice>."
            )

    @staticmethod
    def _build_flow_prompt(node: FlowNode, edges: list[FlowEdge]) -> str | list[ContentPart]:
        """构建流程节点的提示词。

        Args:
            node: 流程节点对象。
            edges: 该节点的出边列表。

        Returns:
            提示词内容。
        """
        if node.kind != "decision":
            return node.label

        if not isinstance(node.label, str):
            label_text = Message(role="user", content=node.label).extract_text(" ")
        else:
            label_text = node.label
        choices = [edge.label for edge in edges if edge.label]
        lines = [
            label_text,
            "",
            "Available branches:",
            *(f"- {choice}" for choice in choices),
            "",
            "Reply with a choice using <choice>...</choice>.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _match_flow_edge(edges: list[FlowEdge], choice: str | None) -> str | None:
        """匹配流程边的选择。

        Args:
            edges: 流程边列表。
            choice: 用户选择文本。

        Returns:
            匹配的目标节点 ID，如果未匹配则返回 None。
        """
        if not choice:
            return None
        for edge in edges:
            if edge.label == choice:
                return edge.dst
        return None

    @staticmethod
    async def _flow_turn(
        soul: NovelSoul,
        prompt: str | list[ContentPart],
    ) -> TurnOutcome:
        """执行流程轮次。

        Args:
            soul: NovelSoul 实例。
            prompt: 提示词内容。

        Returns:
            轮次执行结果。
        """
        wire_send(TurnBegin(user_input=prompt))
        res = await soul._turn(Message(role="user", content=prompt))  # type: ignore[reportPrivateUsage]
        wire_send(TurnEnd())
        return res
