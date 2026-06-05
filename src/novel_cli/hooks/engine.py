"""Hook 引擎模块，管理钩子定义的加载和匹配执行，支持服务端和客户端钩子。"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from novel_cli import logger
from novel_cli.hooks.config import HookDef, HookEventType
from novel_cli.hooks.runner import HookResult, run_hook

# Wire 集成相关的回调签名
type OnTriggered = Callable[[str, str, int], None]
"""(事件, 目标, 钩子数量) -> None"""

type OnResolved = Callable[[str, str, str, str, int], None]
"""(事件, 目标, 决策, 原因, 持续时间毫秒) -> None"""

type OnWireHookRequest = Callable[[WireHookHandle], Awaitable[None]]
"""当 wire 钩子需要客户端处理时调用。回调应通过 wire 发送请求，
并在客户端响应时解析 handle。"""


@dataclass
class WireHookSubscription:
    """通过 wire initialize 注册的客户端钩子订阅。

    Attributes:
        id: 订阅的唯一标识符。
        event: 触发此钩子的事件类型。
        matcher: 用于过滤的正则表达式模式，空字符串匹配所有。
        timeout: 执行超时时间（秒）。
    """

    id: str
    event: str
    matcher: str = ""
    timeout: int = 30


@dataclass
class WireHookHandle:
    """等待客户端响应的 wire 钩子请求句柄。

    Attributes:
        id: 请求的唯一标识符。
        subscription_id: 关联的订阅 ID。
        event: 事件类型。
        target: 匹配目标字符串。
        input_data: 发送给客户端的输入数据。
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    subscription_id: str = ""
    event: str = ""
    target: str = ""
    input_data: dict[str, Any] = field(default_factory=lambda: {})
    _future: asyncio.Future[HookResult] | None = field(default=None, repr=False)

    def _get_future(self) -> asyncio.Future[HookResult]:
        """获取或创建用于等待响应的 Future 对象。

        Returns:
            asyncio.Future 对象，用于等待客户端响应。
        """
        if self._future is None:
            self._future = asyncio.get_event_loop().create_future()
        return self._future

    async def wait(self) -> HookResult:
        """等待客户端响应。

        Returns:
            HookResult 对象，包含客户端的决策。
        """
        return await self._get_future()

    def resolve(self, action: str = "allow", reason: str = "") -> None:
        """使用客户端决策解析请求。

        Args:
            action: 决策类型，"allow" 或 "block"。
            reason: 阻断原因，仅在 action 为 "block" 时有意义。
        """
        result = HookResult(action=action, reason=reason)  # type: ignore[arg-type]
        future = self._get_future()
        if not future.done():
            future.set_result(result)


class HookEngine:
    """加载钩子定义并并行执行匹配的钩子。

    支持两种钩子来源：
    - 服务端（config.toml）：本地执行的 Shell 命令
    - 客户端（wire subscriptions）：通过 HookRequest 转发给客户端

    Attributes:
        _hooks: 服务端钩子定义列表。
        _wire_subs: wire 钩子订阅列表。
        _cwd: 钩子执行的工作目录。
        _on_triggered: HookTriggered 事件回调。
        _on_resolved: HookResolved 事件回调。
        _on_wire_hook: wire 钩子请求回调。
        _by_event: 按事件类型索引的服务端钩子映射。
        _wire_by_event: 按事件类型索引的 wire 钩子映射。
    """

    def __init__(
        self,
        hooks: list[HookDef] | None = None,
        cwd: str | None = None,
        *,
        on_triggered: OnTriggered | None = None,
        on_resolved: OnResolved | None = None,
        on_wire_hook: OnWireHookRequest | None = None,
    ):
        """初始化钩子引擎实例。

        Args:
            hooks: 初始的服务端钩子定义列表。
            cwd: 钩子执行的工作目录。
            on_triggered: HookTriggered 事件回调函数。
            on_resolved: HookResolved 事件回调函数。
            on_wire_hook: wire 钩子请求回调函数。
        """
        self._hooks: list[HookDef] = list(hooks) if hooks else []
        self._wire_subs: list[WireHookSubscription] = []
        self._cwd = cwd
        self._on_triggered = on_triggered
        self._on_resolved = on_resolved
        self._on_wire_hook = on_wire_hook
        self._by_event: dict[str, list[HookDef]] = {}
        self._wire_by_event: dict[str, list[WireHookSubscription]] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """重建事件类型到钩子的索引映射。"""
        self._by_event.clear()
        for h in self._hooks:
            self._by_event.setdefault(h.event, []).append(h)
        self._wire_by_event.clear()
        for s in self._wire_subs:
            self._wire_by_event.setdefault(s.event, []).append(s)

    def add_hooks(self, hooks: list[HookDef]) -> None:
        """运行时添加服务端钩子，重建索引。

        Args:
            hooks: 要添加的钩子定义列表。
        """
        self._hooks.extend(hooks)
        self._rebuild_index()

    def add_wire_subscriptions(self, subs: list[WireHookSubscription]) -> None:
        """从 wire initialize 注册客户端钩子订阅。

        Args:
            subs: 要注册的订阅列表。
        """
        self._wire_subs.extend(subs)
        self._rebuild_index()

    def set_callbacks(
        self,
        on_triggered: OnTriggered | None = None,
        on_resolved: OnResolved | None = None,
        on_wire_hook: OnWireHookRequest | None = None,
    ) -> None:
        """设置 wire 事件回调函数。

        Args:
            on_triggered: HookTriggered 事件回调函数。
            on_resolved: HookResolved 事件回调函数。
            on_wire_hook: wire 钩子请求回调函数。
        """
        self._on_triggered = on_triggered
        self._on_resolved = on_resolved
        self._on_wire_hook = on_wire_hook

    @property
    def has_hooks(self) -> bool:
        """检查是否存在任何已注册的钩子。

        Returns:
            True 如果存在服务端或客户端钩子，否则 False。
        """
        return bool(self._hooks) or bool(self._wire_subs)

    def has_hooks_for(self, event: HookEventType) -> bool:
        """检查指定事件类型是否存在已注册的钩子。

        Args:
            event: 要检查的事件类型。

        Returns:
            True 如果存在针对该事件的钩子，否则 False。
        """
        return bool(self._by_event.get(event)) or bool(self._wire_by_event.get(event))

    @property
    def summary(self) -> dict[str, int]:
        """获取事件类型到钩子总数的映射。

        Returns:
            字典，键为事件类型，值为服务端和客户端钩子总数。
        """
        counts: dict[str, int] = {}
        for event, hooks in self._by_event.items():
            counts[event] = counts.get(event, 0) + len(hooks)
        for event, subs in self._wire_by_event.items():
            counts[event] = counts.get(event, 0) + len(subs)
        return counts

    def details(self) -> dict[str, list[dict[str, str]]]:
        """获取事件类型到钩子详情列表的映射，用于显示。

        Returns:
            字典，键为事件类型，值为钩子详情列表，每个详情包含
            matcher、source 和 command 字段。
        """
        result: dict[str, list[dict[str, str]]] = {}
        for event, hooks in self._by_event.items():
            entries = result.setdefault(event, [])
            for h in hooks:
                entries.append(
                    {
                        "matcher": h.matcher or "(all)",
                        "source": "server",
                        "command": h.command,
                    }
                )
        for event, subs in self._wire_by_event.items():
            entries = result.setdefault(event, [])
            for s in subs:
                entries.append(
                    {
                        "matcher": s.matcher or "(all)",
                        "source": "wire",
                        "command": "(client-side)",
                    }
                )
        return result

    def _match_regex(self, pattern: str, value: str) -> bool:
        """使用正则表达式匹配值。

        Args:
            pattern: 正则表达式模式，空字符串匹配所有。
            value: 待匹配的值。

        Returns:
            True 如果匹配成功或模式为空，否则 False。
        """
        if not pattern:
            return True
        try:
            return bool(re.search(pattern, value))
        except re.error:
            logger.warning("Invalid regex in hook matcher: {}", pattern)
            return False

    async def trigger(
        self,
        event: HookEventType,
        *,
        matcher_value: str = "",
        input_data: dict[str, Any],
    ) -> list[HookResult]:
        """并行运行所有匹配的钩子（服务端 + 客户端）。

        Args:
            event: 触发的钩子事件类型。
            matcher_value: 用于正则匹配的值，如工具名称。
            input_data: 通过 stdin 传递给钩子的输入数据。

        Returns:
            所有匹配钩子的执行结果列表，无匹配钩子时返回空列表。
        """
        # --- 匹配服务端钩子 ---
        seen_commands: set[str] = set()
        server_matched: list[HookDef] = []
        for h in self._by_event.get(event, []):
            if not self._match_regex(h.matcher, matcher_value):
                continue
            if h.command in seen_commands:
                continue
            seen_commands.add(h.command)
            server_matched.append(h)

        # --- 匹配 wire 订阅 ---
        wire_matched: list[WireHookSubscription] = []
        for s in self._wire_by_event.get(event, []):
            if not self._match_regex(s.matcher, matcher_value):
                continue
            wire_matched.append(s)

        total = len(server_matched) + len(wire_matched)
        if total == 0:
            return []

        try:
            return await self._execute_hooks(
                event, matcher_value, server_matched, wire_matched, input_data
            )
        except Exception:
            logger.warning("Hook engine error for {}, failing open", event)
            return []

    async def _execute_hooks(
        self,
        event: str,
        matcher_value: str,
        server_matched: list[HookDef],
        wire_matched: list[WireHookSubscription],
        input_data: dict[str, Any],
    ) -> list[HookResult]:
        """执行匹配的钩子并发送 wire 事件。单独提取用于 fail-open 包装。

        Args:
            event: 事件类型名称。
            matcher_value: 用于正则匹配的值。
            server_matched: 匹配的服务端钩子列表。
            wire_matched: 匹配的 wire 订阅列表。
            input_data: 输入数据。

        Returns:
            所有钩子的执行结果列表。
        """
        total = len(server_matched) + len(wire_matched)
        logger.debug(
            "Triggering {} hooks for {} ({} server, {} wire)",
            total,
            event,
            len(server_matched),
            len(wire_matched),
        )

        # --- HookTriggered ---
        if self._on_triggered:
            try:
                self._on_triggered(event, matcher_value, total)
            except Exception:
                logger.debug("HookTriggered callback failed, continuing")

        t0 = time.monotonic()
        tasks: list[asyncio.Task[HookResult]] = []

        # 服务端：运行 Shell 命令
        for h in server_matched:
            tasks.append(
                asyncio.create_task(
                    run_hook(h.command, input_data, timeout=h.timeout, cwd=self._cwd)
                )
            )

        # 客户端：发送请求并等待响应
        for s in wire_matched:
            tasks.append(
                asyncio.create_task(
                    self._dispatch_wire_hook(
                        s.id, event, matcher_value, input_data, timeout=s.timeout
                    )
                )
            )

        results = list(await asyncio.gather(*tasks))
        duration_ms = int((time.monotonic() - t0) * 1000)

        # 聚合结果：任意钩子阻断则整体阻断
        action = "allow"
        reason = ""
        for r in results:
            if r.action == "block":
                action = "block"
                reason = r.reason
                break

        # --- HookResolved ---
        if self._on_resolved:
            try:
                self._on_resolved(event, matcher_value, action, reason, duration_ms)
            except Exception:
                logger.debug("HookResolved callback failed, continuing")

        return results

    async def _dispatch_wire_hook(
        self,
        subscription_id: str,
        event: str,
        target: str,
        input_data: dict[str, Any],
        *,
        timeout: int = 30,
    ) -> HookResult:
        """发送钩子请求到 wire 客户端并等待响应。

        Args:
            subscription_id: 订阅的唯一标识符。
            event: 事件类型名称。
            target: 匹配目标字符串。
            input_data: 发送给客户端的输入数据。
            timeout: 执行超时时间（秒）。

        Returns:
            HookResult 对象，包含客户端的决策。
        """
        if not self._on_wire_hook:
            return HookResult(action="allow")

        handle = WireHookHandle(
            subscription_id=subscription_id,
            event=event,
            target=target,
            input_data=input_data,
        )
        # 在后台运行回调，使超时应用于完整的客户端往返，
        # 而不仅仅是 handle.wait()
        hook_task: asyncio.Task[None] = asyncio.ensure_future(self._on_wire_hook(handle))
        hook_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        try:
            return await asyncio.wait_for(handle.wait(), timeout=timeout)
        except TimeoutError:
            hook_task.cancel()
            logger.warning("Wire hook timed out: {} {}", event, target)
            return HookResult(action="allow", timed_out=True)
        except Exception as e:
            hook_task.cancel()
            logger.warning("Wire hook failed: {} {}: {}", event, target, e)
            return HookResult(action="allow")