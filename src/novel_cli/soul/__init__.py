"""soul 核心模块。

本模块定义了 soul 的核心接口和运行机制，包括 Soul 协议、状态管理、
运行控制以及与 Wire 的通信接口。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from novel_cli.hooks.engine import HookEngine
from novel_cli.utils.aioqueue import QueueShutDown
from novel_cli.utils.logging import logger
from novel_cli.wire import Wire
from novel_cli.wire.file import WireFile
from novel_cli.wire.types import ContentPart, MCPStatusSnapshot, WireMessage

if TYPE_CHECKING:
    from novel_cli.llm import LLM, ModelCapability
    from novel_cli.soul.agent import Runtime
    from novel_cli.utils.slashcmd import SlashCommand


class LLMNotSet(Exception):
    """当 LLM 未设置时抛出的异常。"""

    def __init__(self) -> None:
        super().__init__("LLM not set")


class LLMNotSupported(Exception):
    """当 LLM 不具备所需能力时抛出的异常。

    Attributes:
        llm: 不支持的 LLM 实例。
        capabilities: 缺失的能力列表。
    """

    def __init__(self, llm: LLM, capabilities: list[ModelCapability]):
        """初始化异常。

        Args:
            llm: 不支持的 LLM 实例。
            capabilities: 缺失的能力列表。
        """
        self.llm = llm
        self.capabilities = capabilities
        capabilities_str = "capability" if len(capabilities) == 1 else "capabilities"
        super().__init__(
            f"LLM model '{llm.model_name}' does not support required {capabilities_str}: "
            f"{', '.join(capabilities)}."
        )


class MaxStepsReached(Exception):
    """当达到最大步数限制时抛出的异常。

    Attributes:
        n_steps: 已执行的步数。
    """

    n_steps: int
    # 已执行的步数

    def __init__(self, n_steps: int):
        """初始化异常。

        Args:
            n_steps: 已执行的步数。
        """
        super().__init__(f"Max number of steps reached: {n_steps}")
        self.n_steps = n_steps


def format_token_count(n: int) -> str:
    """将 token 数量格式化为紧凑字符串。

    例如：28.5k、128k、1.2m。

    Args:
        n: token 数量。

    Returns:
        格式化后的字符串。
    """
    suffix = ""
    if n >= 1_000_000:
        value = n / 1_000_000
        suffix = "m"
    elif n >= 1_000:
        value = n / 1_000
        suffix = "k"
    else:
        return str(n)

    # 当需要时保留一位小数，但去掉末尾的 ".0"
    compact = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{compact}{suffix}"


def format_context_status(
    context_usage: float,
    context_tokens: int = 0,
    max_context_tokens: int = 0,
) -> str:
    """格式化上下文状态字符串，用于状态栏显示。

    Args:
        context_usage: 上下文使用率（百分比）。
        context_tokens: 当前使用的 token 数量。
        max_context_tokens: 最大可用的 token 数量。

    Returns:
        格式化后的状态字符串。
    """
    bounded = max(0.0, min(context_usage, 1.0))
    if max_context_tokens > 0:
        used = format_token_count(context_tokens)
        total = format_token_count(max_context_tokens)
        return f"context: {bounded:.1%} ({used}/{total})"
    return f"context: {bounded:.1%}"


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    """状态快照，记录 soul 的当前状态信息。

    Attributes:
        context_usage: 上下文使用率（百分比）。
        yolo_enabled: 是否启用 YOLO（自动批准）模式。
        plan_mode: 是否处于计划模式（只读研究和规划）。
        context_tokens: 当前上下文中的 token 数量。
        max_context_tokens: 上下文可容纳的最大 token 数量。
        mcp_status: 当前的 MCP 启动快照（如果已配置 MCP）。
    """

    context_usage: float
    # 上下文使用率（百分比）
    yolo_enabled: bool = False
    # 是否启用 YOLO（自动批准）模式
    plan_mode: bool = False
    # 是否处于计划模式（只读研究和规划）
    context_tokens: int = 0
    # 当前上下文中的 token 数量
    max_context_tokens: int = 0
    # 上下文可容纳的最大 token 数量
    mcp_status: MCPStatusSnapshot | None = None
    # 当前的 MCP 启动快照（如果已配置 MCP）


@runtime_checkable
class Soul(Protocol):
    """soul 协议，定义了 soul 的核心接口。"""

    @property
    def name(self) -> str:
        """soul 的名称。"""
        ...

    @property
    def model_name(self) -> str:
        """soul 使用的 LLM 模型名称，如果 LLM 未设置则为空字符串。"""
        ...

    @property
    def model_capabilities(self) -> set[ModelCapability] | None:
        """soul 使用的 LLM 模型的能力集合，如果 LLM 未设置则为 None。"""
        ...

    @property
    def thinking(self) -> bool | None:
        """思考模式是否启用。

        如果 LLM 未设置或思考模式未显式设置则为 None。
        """
        ...

    @property
    def status(self) -> StatusSnapshot:
        """soul 的当前状态，返回值是不可变的。"""
        ...

    @property
    def hook_engine(self) -> HookEngine:
        """soul 的钩子引擎。"""
        ...

    @property
    def available_slash_commands(self) -> list[SlashCommand[Any]]:
        """soul 支持的可用斜杠命令列表。"""
        ...

    async def run(self, user_input: str | list[ContentPart]):
        """使用给定的用户输入运行 agent，直到达到最大步数或没有更多工具调用。

        Args:
            user_input: 用户输入给 agent 的内容，
                可以是斜杠命令调用或自然语言输入。

        Raises:
            LLMNotSet: 当 LLM 未设置时抛出。
            LLMNotSupported: 当 LLM 不具备所需能力时抛出。
            ChatProviderError: 当 LLM provider 返回错误时抛出。
            MaxStepsReached: 当达到最大步数时抛出。
            asyncio.CancelledError: 当运行被用户取消时抛出。
        """
        ...


type UILoopFn = Callable[[Wire], Coroutine[Any, Any, None]]
"""用于可视化 agent 行为的长时间运行的异步函数。"""


class RunCancelled(Exception):
    """运行被取消事件取消时抛出的异常。"""


async def run_soul(
    soul: Soul,
    user_input: str | list[ContentPart],
    ui_loop_fn: UILoopFn,
    cancel_event: asyncio.Event,
    wire_file: WireFile | None = None,
    runtime: Runtime | None = None,
) -> None:
    """使用给定的用户输入运行 soul，通过 Wire 连接到 UI 循环。

    cancel_event 是一个外部句柄，可用于取消运行。当事件被设置时，
    运行将优雅停止并抛出 RunCancelled 异常。

    Args:
        soul: 要运行的 soul 实例。
        user_input: 用户输入内容。
        ui_loop_fn: UI 循环函数。
        cancel_event: 取消事件句柄。
        wire_file: Wire 文件后端（可选）。
        runtime: 运行时实例（可选）。

    Raises:
        LLMNotSet: 当 LLM 未设置时抛出。
        LLMNotSupported: 当 LLM 不具备所需能力时抛出。
        ChatProviderError: 当 LLM provider 返回错误时抛出。
        MaxStepsReached: 当达到最大步数时抛出。
        RunCancelled: 当运行被取消事件取消时抛出。
    """
    wire = Wire(file_backend=wire_file)
    wire_token = _current_wire.set(wire)

    logger.debug("Starting UI loop with function: {ui_loop_fn}", ui_loop_fn=ui_loop_fn)
    ui_task = asyncio.create_task(ui_loop_fn(wire))

    logger.debug("Starting soul run")
    soul_task = asyncio.create_task(soul.run(user_input))
    notification_task = asyncio.create_task(_pump_notifications_to_wire(runtime, wire))

    cancel_event_task = asyncio.create_task(cancel_event.wait())
    await asyncio.wait(
        [soul_task, cancel_event_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    try:
        if cancel_event.is_set():
            logger.debug("Cancelling the run task")
            soul_task.cancel()
            try:
                await soul_task
            except asyncio.CancelledError:
                raise RunCancelled from None
        else:
            assert soul_task.done()  # 停止事件已设置或运行任务已完成
            cancel_event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_event_task
            soul_task.result()  # 如果运行任务中抛出了异常，这里会抛出
    finally:
        notification_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await notification_task
        try:
            await _deliver_notifications_to_wire_once(runtime, wire)
        except Exception:
            logger.exception("Failed to flush notifications to wire during shutdown")
        logger.debug("Shutting down the UI loop")
        # 关闭 wire 应该会中断 UI 循环
        wire.shutdown()
        await wire.join()
        try:
            await asyncio.wait_for(ui_task, timeout=0.5)
        except QueueShutDown:
            logger.debug("UI loop shut down")
            pass
        except TimeoutError:
            logger.warning("UI loop timed out")
        finally:
            _current_wire.reset(wire_token)


_current_wire = ContextVar[Wire | None]("current_wire", default=None)


def get_wire_or_none() -> Wire | None:
    """获取当前的 Wire，如果没有则返回 None。

    在 agent 循环中的任何位置调用时，预期返回值不为 None。
    """
    return _current_wire.get()


def wire_send(msg: WireMessage) -> None:
    """向当前 Wire 发送消息。

    这相当于 soul 的 print 和 input 函数。
    soul 应始终使用此函数发送 wire 消息。

    Args:
        msg: 要发送的 Wire 消息。
    """
    wire = get_wire_or_none()
    assert wire is not None, "Wire is expected to be set when soul is running"
    wire.soul_side.send(msg)


async def _pump_notifications_to_wire(runtime: Runtime | None, wire: Wire) -> None:
    """持续将通知泵送到 Wire。

    Args:
        runtime: 运行时实例。
        wire: Wire 实例。
    """
    while True:
        try:
            await _deliver_notifications_to_wire_once(runtime, wire)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Notification wire pump failed")
        await asyncio.sleep(1.0)


async def _deliver_notifications_to_wire_once(runtime: Runtime | None, wire: Wire) -> None:
    """一次性将待处理的通知投递到 Wire。

    Args:
        runtime: 运行时实例。
        wire: Wire 实例。
    """
    if runtime is None or runtime.role != "root":
        return

    from novel_cli.notifications import NotificationView, to_wire_notification

    def _send_notification(view: NotificationView) -> None:
        wire.soul_side.send(to_wire_notification(view))

    await runtime.notifications.deliver_pending(
        "wire",
        limit=8,
        before_claim=runtime.background_tasks.reconcile,
        on_notification=_send_notification,
    )
