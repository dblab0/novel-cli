"""Wire JSON-RPC 服务器模块。

本模块实现 Wire 协议的 JSON-RPC 服务器，通过 stdio 与客户端通信。
服务器负责：
- 处理客户端的初始化、提示、转向、回放、取消等请求
- 将 Soul 产生的消息流式发送到客户端
- 处理客户端对审批、工具调用、问题等请求的响应
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Literal, cast

import acp  # type: ignore[reportMissingTypeStubs]
import pydantic
from kosong.chat_provider import APIStatusError, ChatProviderError
from kosong.tooling import ToolError, ToolResult
from kosong.utils.typing import JsonType

from novel_cli.approval_runtime import ApprovalRuntime
from novel_cli.constant import USER_AGENT
from novel_cli.soul import LLMNotSet, LLMNotSupported, MaxStepsReached, RunCancelled, Soul, run_soul
from novel_cli.soul.novelsoul import NovelSoul
from novel_cli.soul.toolset import NovelToolset, WireExternalTool
from novel_cli.utils.aioqueue import Queue, QueueShutDown
from novel_cli.utils.logging import logger
from novel_cli.utils.signals import install_sigint_handler
from novel_cli.wire import Wire
from novel_cli.wire.types import (
    ApprovalRequest,
    ApprovalResponse,
    HookRequest,
    HookResponse,
    QuestionNotSupported,
    QuestionRequest,
    QuestionResponse,
    Request,
    StatusUpdate,
    ToolCallRequest,
    is_event,
    is_request,
)

from .jsonrpc import (
    ClientInfo,
    ErrorCodes,
    JSONRPCCancelMessage,
    JSONRPCErrorObject,
    JSONRPCErrorResponse,
    JSONRPCErrorResponseNullableID,
    JSONRPCEventMessage,
    JSONRPCInitializeMessage,
    JSONRPCInMessage,
    JSONRPCInMessageAdapter,
    JSONRPCMessage,
    JSONRPCOutMessage,
    JSONRPCPromptMessage,
    JSONRPCReplayMessage,
    JSONRPCRequestMessage,
    JSONRPCSetPlanModeMessage,
    JSONRPCSteerMessage,
    JSONRPCSuccessResponse,
    Statuses,
)

# stdio StreamReader 的最大缓冲区大小。
# 作为 `limit` 参数传递给 `acp.stdio_streams`，用于限制从 stdin 读取时的缓冲区大小
# （例如通过 JSON-RPC 发送的大型工具或模型输出）。100MB 的限制足以处理典型的
# 交互式使用场景，同时仍能保护进程免受无限内存增长或缓冲区溢出错误的影响，
# 当对端发送意外的大型 payload 时。
STDIO_BUFFER_LIMIT = 100 * 1024 * 1024


class WireServer:
    """Wire JSON-RPC 服务器。

    通过 stdio 与 Wire 客户端通信，处理 JSON-RPC 请求和响应。
    支持 initialize、prompt、steer、replay、cancel 等方法。

    Attributes:
        _reader: asyncio StreamReader，用于读取 stdin。
        _writer: asyncio StreamWriter，用于写入 stdout。
        _write_task: 写入循环异步任务。
        _write_queue: 出站消息队列。
        _dispatch_tasks: 消息分发任务集合。
        _soul: Soul 实例。
        _cancel_event: 取消事件，用于中断当前轮转。
        _pending_requests: 等待响应的请求映射。
        _client_supports_question: 客户端是否支持 QuestionRequest。
        _client_supports_plan_mode: 客户端是否支持计划模式。
        _initialized: 是否已完成初始化。
        _root_hub_queue: RootWireHub 订阅队列。
        _root_hub_task: RootWireHub 消费任务。
    """

    def __init__(self, soul: Soul):
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        # 出站
        self._write_task: asyncio.Task[None] | None = None
        self._write_queue: Queue[JSONRPCOutMessage] = Queue()

        # 入站
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

        # Soul 运行相关
        self._soul = soul
        self._cancel_event: asyncio.Event | None = None
        self._pending_requests: dict[str, Request] = {}
        self._client_supports_question: bool = False
        self._client_supports_plan_mode: bool = False
        self._initialized: bool = False
        self._root_hub_queue: Queue[Any] | None = None
        self._root_hub_task: asyncio.Task[None] | None = None

    @property
    def _approval_runtime(self) -> ApprovalRuntime | None:
        """获取审批运行时（如果可用）。

        Returns:
            ApprovalRuntime 实例或 None。
        """
        if isinstance(self._soul, NovelSoul):
            return self._soul.runtime.approval_runtime
        return None

    async def serve(self) -> None:
        """启动 Wire 服务器，监听 stdio。

        服务器将：
        - 从 stdin 读取 JSON-RPC 消息
        - 将响应和事件写入 stdout
        - 处理 SIGINT 中断信号
        """
        logger.info("Starting Wire server on stdio")

        self._reader, self._writer = await acp.stdio_streams(limit=STDIO_BUFFER_LIMIT)
        self._write_task = asyncio.create_task(self._write_loop())
        if isinstance(self._soul, NovelSoul) and self._soul.runtime.root_wire_hub is not None:
            self._root_hub_queue = self._soul.runtime.root_wire_hub.subscribe()
            self._root_hub_task = asyncio.create_task(self._root_hub_loop())
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        remove_sigint = install_sigint_handler(loop, stop_event.set)
        read_task = asyncio.create_task(self._read_loop())
        stop_task = asyncio.create_task(stop_event.wait())
        tasks: set[asyncio.Task[Any]] = {read_task, stop_task}
        pending = tasks
        try:
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_event.is_set():
                logger.info("Wire server interrupted, shutting down")
                if self._cancel_event is not None:
                    self._cancel_event.set()
                if not read_task.done():
                    read_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await read_task
            elif read_task in done:
                read_task.result()
        except KeyboardInterrupt:
            logger.info("Wire server interrupted, shutting down")
            if self._cancel_event is not None:
                self._cancel_event.set()
        finally:
            remove_sigint()
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self._shutdown()

    async def _root_hub_loop(self) -> None:
        """RootWireHub 消息消费循环。

        处理来自 RootWireHub 的非轮转顺序消息。
        """
        assert self._root_hub_queue is not None
        while True:
            try:
                msg = await self._root_hub_queue.get()
            except QueueShutDown:
                return
            try:
                if not self._initialized:
                    continue
                if isinstance(msg, ApprovalRequest):
                    await self._request_approval(msg)
                elif isinstance(msg, ApprovalResponse):
                    self._pending_requests.pop(msg.request_id, None)
                    await self._send_msg(JSONRPCEventMessage(method="event", params=msg))
                elif is_event(msg):
                    await self._send_msg(JSONRPCEventMessage(method="event", params=msg))
            except Exception:
                logger.exception("Root hub message handling failed")

    async def _write_loop(self) -> None:
        """写入循环，持续发送出站消息到 stdout。"""
        assert self._writer is not None

        try:
            while True:
                try:
                    msg = await self._write_queue.get()
                except QueueShutDown:
                    logger.debug("Send queue shut down, stopping Wire server write loop")
                    break
                self._writer.write(msg.model_dump_json().encode("utf-8") + b"\n")
                await self._writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Wire server write loop error:")
            raise

    async def _read_loop(self) -> None:
        """读取循环，持续从 stdin 读取并处理入站消息。"""
        assert self._reader is not None

        while True:
            raw_line = await self._reader.readline()
            if not raw_line:
                logger.info("stdin closed, Wire server exiting")
                break
            line = raw_line.decode("utf-8", errors="replace").strip()

            try:
                msg_json = json.loads(line)
            except ValueError:
                logger.error("Invalid JSON line: {line}", line=line)
                await self._send_msg(
                    JSONRPCErrorResponseNullableID(
                        id=None,
                        error=JSONRPCErrorObject(
                            code=ErrorCodes.PARSE_ERROR,
                            message="Invalid JSON format",
                        ),
                    )
                )
                continue

            try:
                generic_msg = JSONRPCMessage.model_validate(msg_json)
            except pydantic.ValidationError as e:
                logger.error("Invalid JSON-RPC message: {error}", error=e)
                await self._send_msg(
                    JSONRPCErrorResponseNullableID(
                        id=None,
                        error=JSONRPCErrorObject(
                            code=ErrorCodes.INVALID_REQUEST,
                            message="Invalid request",
                        ),
                    )
                )
                continue

            if generic_msg.is_response():
                # 对于响应，跳过方法检查
                try:
                    msg = JSONRPCInMessageAdapter.validate_python(msg_json)
                except pydantic.ValidationError as e:
                    logger.error("Invalid JSON-RPC response: {error}", error=e)
                    await self._send_msg(
                        JSONRPCErrorResponseNullableID(
                            id=None,
                            error=JSONRPCErrorObject(
                                code=ErrorCodes.INVALID_REQUEST,
                                message="Invalid response",
                            ),
                        )
                    )
                    continue  # 忽略无效的 JSON-RPC 响应

                if not isinstance(msg, (JSONRPCSuccessResponse, JSONRPCErrorResponse)):
                    logger.error(
                        "Invalid JSON-RPC response message: {msg}",
                        msg=msg_json,
                    )
                    continue  # 忽略无效的响应消息

                task = asyncio.create_task(self._dispatch_msg(msg))
                task.add_done_callback(self._dispatch_tasks.discard)
                self._dispatch_tasks.add(task)
                continue

            if not generic_msg.method_is_inbound():
                logger.error(
                    "Unexpected JSON-RPC method received: {method}",
                    method=generic_msg.method,
                )
                if generic_msg.id is not None:
                    resp = JSONRPCErrorResponse(
                        id=generic_msg.id,
                        error=JSONRPCErrorObject(
                            code=ErrorCodes.METHOD_NOT_FOUND,
                            message=f"Unexpected method received: {generic_msg.method}",
                        ),
                    )
                    await self._send_msg(resp)
                continue  # 忽略意外的出站方法

            try:
                msg = JSONRPCInMessageAdapter.validate_python(msg_json)
            except pydantic.ValidationError as e:
                logger.error("Invalid JSON-RPC inbound message: {error}", error=e)
                if generic_msg.id is not None:
                    resp = JSONRPCErrorResponse(
                        id=generic_msg.id,
                        error=JSONRPCErrorObject(
                            code=ErrorCodes.INVALID_PARAMS,
                            message=f"Invalid parameters for method `{generic_msg.method}`",
                        ),
                    )
                    await self._send_msg(resp)
                continue  # 忽略无效的入站消息

            task = asyncio.create_task(self._dispatch_msg(msg))
            task.add_done_callback(self._dispatch_tasks.discard)
            self._dispatch_tasks.add(task)

    async def _shutdown(self) -> None:
        """关闭服务器，清理所有资源和待处理请求。"""
        for request in self._pending_requests.values():
            if request.resolved:
                continue
            match request:
                case ApprovalRequest():
                    if request.source_kind == "foreground_turn":
                        request.resolve("reject")
                        if self._approval_runtime is not None:
                            self._approval_runtime.resolve(request.id, "reject")
                case ToolCallRequest():
                    request.resolve(
                        ToolError(
                            message="Wire connection closed before tool result was received.",
                            brief="Wire closed",
                        )
                    )
                case QuestionRequest():
                    request.resolve({})
                case HookRequest():
                    request.resolve("allow")
        self._pending_requests.clear()

        if self._cancel_event is not None:
            self._cancel_event.set()
            self._cancel_event = None

        self._write_queue.shutdown()
        if self._write_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._write_task

        if self._root_hub_task is not None:
            self._root_hub_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._root_hub_task
            self._root_hub_task = None
        if (
            isinstance(self._soul, NovelSoul)
            and self._root_hub_queue is not None
            and self._soul.runtime.root_wire_hub is not None
        ):
            self._soul.runtime.root_wire_hub.unsubscribe(self._root_hub_queue)
            self._root_hub_queue = None

        await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
        self._dispatch_tasks.clear()

        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None

        self._reader = None
        self._initialized = False

    async def _dispatch_msg(self, msg: JSONRPCInMessage) -> None:
        """分发并处理入站 JSON-RPC 消息。

        Args:
            msg: 入站的 JSON-RPC 消息。
        """
        resp: JSONRPCSuccessResponse | JSONRPCErrorResponse | None = None
        try:
            match msg:
                case JSONRPCInitializeMessage():
                    resp = await self._handle_initialize(msg)
                case JSONRPCPromptMessage():
                    resp = await self._handle_prompt(msg)
                case JSONRPCReplayMessage():
                    resp = await self._handle_replay(msg)
                case JSONRPCSteerMessage():
                    resp = await self._handle_steer(msg)
                case JSONRPCSetPlanModeMessage():
                    resp = await self._handle_set_plan_mode(msg)
                case JSONRPCCancelMessage():
                    resp = await self._handle_cancel(msg)
                case JSONRPCSuccessResponse() | JSONRPCErrorResponse():
                    await self._handle_response(msg)

            if resp is not None:
                await self._send_msg(resp)
        except Exception:
            logger.exception("Unexpected error dispatching JSONRPC message:")
            raise

    async def _send_msg(self, msg: JSONRPCOutMessage) -> None:
        """发送出站消息到写入队列。

        Args:
            msg: 要发送的出站消息。
        """
        try:
            await self._write_queue.put(msg)
        except QueueShutDown:
            logger.error("Send queue shut down; dropping message: {msg}", msg=msg)

    @property
    def _is_streaming(self) -> bool:
        """是否有轮转正在运行。"""
        return self._cancel_event is not None

    async def _handle_initialize(
        self, msg: JSONRPCInitializeMessage
    ) -> JSONRPCSuccessResponse | JSONRPCErrorResponse:
        """处理初始化请求。

        Args:
            msg: 初始化请求消息。

        Returns:
            成功或错误响应。
        """
        if self._is_streaming:
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(
                    code=ErrorCodes.INVALID_STATE,
                    message="An agent turn is already in progress",
                ),
            )

        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        toolset = None
        if isinstance(self._soul, NovelSoul) and isinstance(self._soul.agent.toolset, NovelToolset):
            toolset = self._soul.agent.toolset

        if toolset and msg.params.external_tools:
            for tool in msg.params.external_tools:
                existing = toolset.find(tool.name)
                if existing is not None and not isinstance(existing, WireExternalTool):
                    rejected.append({"name": tool.name, "reason": "conflicts with builtin tool"})
                    continue
                ok, reason = toolset.register_external_tool(
                    tool.name,
                    tool.description,
                    tool.parameters,
                )
                if ok:
                    accepted.append(tool.name)
                else:
                    rejected.append({"name": tool.name, "reason": reason or "invalid schema"})

        slash_commands: list[JsonType] = []
        for cmd in self._soul.available_slash_commands:
            slash_commands.append(
                cast(
                    JsonType,
                    {"name": cmd.name, "description": cmd.description, "aliases": cmd.aliases},
                )
            )

        from novel_cli.constant import NAME, VERSION
        from novel_cli.hooks.config import HOOK_EVENT_TYPES
        from novel_cli.hooks.engine import WireHookHandle, WireHookSubscription
        from novel_cli.soul import wire_send
        from novel_cli.wire.protocol import WIRE_PROTOCOL_VERSION
        from novel_cli.wire.types import HookResolved, HookTriggered

        # Hook 引擎设置 —— 注册 wire 订阅和回调

        hook_engine = self._soul.hook_engine

        if msg.params.hooks:
            wire_subs: list[WireHookSubscription] = []
            for wh in msg.params.hooks:
                if wh.event not in HOOK_EVENT_TYPES:
                    logger.warning("Ignoring unknown hook event from client: {}", wh.event)
                    continue
                wire_subs.append(
                    WireHookSubscription(
                        id=wh.id,
                        event=wh.event,
                        matcher=wh.matcher,
                        timeout=wh.timeout,
                    )
                )
            if wire_subs:
                hook_engine.add_wire_subscriptions(wire_subs)
                logger.info("Registered {} wire hook subscriptions from client", len(wire_subs))

        def _on_triggered(event: str, target: str, count: int) -> None:
            wire_send(HookTriggered(event=event, target=target, hook_count=count))

        def _on_resolved(
            event: str,
            target: str,
            action: str,
            reason: str,
            duration_ms: int,
        ) -> None:
            wire_send(
                HookResolved(
                    event=event,
                    target=target,
                    action=cast(Literal["allow", "block"], action),
                    reason=reason,
                    duration_ms=duration_ms,
                )
            )

        async def _on_wire_hook(handle: WireHookHandle) -> None:
            """发送 HookRequest 到客户端，将 wire 响应返回给 handle。"""
            request = HookRequest(
                id=handle.id,
                subscription_id=handle.subscription_id,
                event=handle.event,
                target=handle.target,
                input_data=handle.input_data,
            )
            self._pending_requests[handle.id] = request
            await self._send_msg(JSONRPCRequestMessage(id=handle.id, params=request))
            # 等待客户端响应（通过 _handle_response 解决）
            action, reason = await request.wait()
            handle.resolve(action, reason)

        hook_engine.set_callbacks(
            on_triggered=_on_triggered,
            on_resolved=_on_resolved,
            on_wire_hook=_on_wire_hook,
        )

        hooks_info: dict[str, JsonType] = cast(
            dict[str, JsonType],
            {
                "supported_events": HOOK_EVENT_TYPES,
                "configured": hook_engine.summary,
            },
        )

        result: dict[str, JsonType] = {
            "protocol_version": WIRE_PROTOCOL_VERSION,
            "server": cast(JsonType, {"name": NAME, "version": VERSION}),
            "slash_commands": cast(JsonType, slash_commands),
        }
        if accepted or rejected:
            result["external_tools"] = cast(
                JsonType,
                {
                    "accepted": accepted,
                    "rejected": rejected,
                },
            )

        if hooks_info:
            result["hooks"] = cast(JsonType, hooks_info)

        self._apply_wire_client_info(msg.params.client)

        if msg.params.capabilities is not None:
            self._client_supports_question = msg.params.capabilities.supports_question
            self._client_supports_plan_mode = msg.params.capabilities.supports_plan_mode

        if toolset is not None:
            self._sync_ask_user_tool_visibility(toolset)
            self._sync_plan_mode_tool_visibility(toolset)

        self._initialized = True
        if self._approval_runtime is not None:
            for request in self._approval_runtime.list_pending():
                await self._request_approval(
                    ApprovalRequest(
                        id=request.id,
                        tool_call_id=request.tool_call_id,
                        sender=request.sender,
                        action=request.action,
                        description=request.description,
                        display=request.display,
                        source_kind=request.source.kind,
                        source_id=request.source.id,
                        agent_id=request.source.agent_id,
                        subagent_type=request.source.subagent_type,
                    )
                )

        result["capabilities"] = cast(
            JsonType,
            {"supports_question": True},
        )

        return JSONRPCSuccessResponse(
            id=msg.id,
            result=result,
        )

    def _sync_ask_user_tool_visibility(self, toolset: NovelToolset) -> None:
        """根据客户端能力隐藏或显示 AskUserQuestion 工具。

        Args:
            toolset: 工具集实例。
        """
        from novel_cli.tools.ask_user import NAME as ASK_USER_TOOL_NAME

        all_toolsets = [toolset]

        if self._client_supports_question:
            for ts in all_toolsets:
                ts.unhide(ASK_USER_TOOL_NAME)
        else:
            for ts in all_toolsets:
                ts.hide(ASK_USER_TOOL_NAME)
            logger.info(
                "Hid {tool} tool: client does not support questions",
                tool=ASK_USER_TOOL_NAME,
            )

    def _sync_plan_mode_tool_visibility(self, toolset: NovelToolset) -> None:
        """根据客户端能力隐藏或显示计划模式工具。

        Args:
            toolset: 工具集实例。
        """
        from novel_cli.tools.plan import NAME as EXIT_PLAN_MODE_TOOL_NAME
        from novel_cli.tools.plan.enter import NAME as ENTER_PLAN_MODE_TOOL_NAME

        plan_tool_names = [ENTER_PLAN_MODE_TOOL_NAME, EXIT_PLAN_MODE_TOOL_NAME]

        all_toolsets = [toolset]

        if self._client_supports_plan_mode:
            for ts in all_toolsets:
                for name in plan_tool_names:
                    ts.unhide(name)
        else:
            for ts in all_toolsets:
                for name in plan_tool_names:
                    ts.hide(name)
            logger.info(
                "Hide plan mode tools: client does not support plan mode",
            )

    def _apply_wire_client_info(self, client: ClientInfo | None) -> None:
        """应用 Wire 客户端信息到 User-Agent。

        Args:
            client: 客户端信息对象。
        """
        if not isinstance(self._soul, NovelSoul):
            return
        llm = self._soul.runtime.llm
        if llm is None:
            return

        ua_suffix = ""
        if client is not None:
            ua_suffix = client.name
            if client.version:
                ua_suffix += f" {client.version}"
            ua_suffix = f" ({ua_suffix.strip()})"

        from kosong.chat_provider.kimi import Kimi

        if isinstance(llm.chat_provider, Kimi):
            novel_client = llm.chat_provider.client
            headers = dict(novel_client._custom_headers)  # pyright: ignore[reportPrivateUsage]
            headers["User-Agent"] = f"{USER_AGENT}{ua_suffix}"
            novel_client._custom_headers = headers  # pyright: ignore[reportPrivateUsage]

    async def _handle_prompt(
        self, msg: JSONRPCPromptMessage
    ) -> JSONRPCSuccessResponse | JSONRPCErrorResponse:
        """处理提示请求。

        Args:
            msg: 提示请求消息。

        Returns:
            成功或错误响应。
        """
        if self._is_streaming:
            # TODO: 支持排队多个输入
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(
                    code=ErrorCodes.INVALID_STATE, message="An agent turn is already in progress"
                ),
            )

        self._cancel_event = asyncio.Event()
        runtime = self._soul.runtime if isinstance(self._soul, NovelSoul) else None
        try:
            await run_soul(
                self._soul,
                msg.params.user_input,
                self._stream_wire_messages,
                self._cancel_event,
                runtime.session.wire_file if runtime else None,
                runtime,
            )
            return JSONRPCSuccessResponse(
                id=msg.id,
                result={"status": Statuses.FINISHED},
            )
        except LLMNotSet:
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(code=ErrorCodes.LLM_NOT_SET, message="LLM is not set"),
            )
        except LLMNotSupported as e:
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(code=ErrorCodes.LLM_NOT_SUPPORTED, message=str(e)),
            )
        except APIStatusError as e:
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(code=ErrorCodes.CHAT_PROVIDER_ERROR, message=str(e)),
            )
        except ChatProviderError as e:
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(code=ErrorCodes.CHAT_PROVIDER_ERROR, message=str(e)),
            )
        except MaxStepsReached as e:
            return JSONRPCSuccessResponse(
                id=msg.id,
                result={"status": Statuses.MAX_STEPS_REACHED, "steps": e.n_steps},
            )
        except RunCancelled:
            return JSONRPCSuccessResponse(
                id=msg.id,
                result={"status": Statuses.CANCELLED},
            )
        finally:
            # 清理此轮转剩余的待处理请求。
            # run_soul() 返回后，soul 和所有子 Agent 都已完成，
            # 所以任何未解决的请求都是过期的。
            stale_ids = [k for k, v in self._pending_requests.items() if not v.resolved]
            for msg_id in stale_ids:
                request = self._pending_requests[msg_id]
                match request:
                    case ApprovalRequest():
                        if request.source_kind == "foreground_turn":
                            self._pending_requests.pop(msg_id, None)
                            request.resolve("reject")
                            if self._approval_runtime is not None:
                                self._approval_runtime.resolve(request.id, "reject")
                    case ToolCallRequest():
                        self._pending_requests.pop(msg_id, None)
                        request.resolve(
                            ToolError(
                                message="Agent turn ended before tool result was received.",
                                brief="Turn ended",
                            )
                        )
                    case QuestionRequest():
                        self._pending_requests.pop(msg_id, None)
                        request.resolve({})
                    case HookRequest():
                        self._pending_requests.pop(msg_id, None)
                        request.resolve("allow")
                    case _:
                        pass
            self._cancel_event = None

    async def _handle_steer(
        self, msg: JSONRPCSteerMessage
    ) -> JSONRPCSuccessResponse | JSONRPCErrorResponse:
        """处理转向请求。

        Args:
            msg: 转向请求消息。

        Returns:
            成功或错误响应。
        """
        if not isinstance(self._soul, NovelSoul) or not self._is_streaming:
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(
                    code=ErrorCodes.INVALID_STATE,
                    message="No agent turn is in progress",
                ),
            )

        self._soul.steer(msg.params.user_input)
        return JSONRPCSuccessResponse(
            id=msg.id,
            result={"status": Statuses.STEERED},
        )

    async def _handle_set_plan_mode(
        self, msg: JSONRPCSetPlanModeMessage
    ) -> JSONRPCSuccessResponse | JSONRPCErrorResponse:
        """处理设置计划模式请求。

        Args:
            msg: 设置计划模式请求消息。

        Returns:
            成功或错误响应。
        """
        if not isinstance(self._soul, NovelSoul):
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(
                    code=ErrorCodes.INVALID_STATE,
                    message="Plan mode is not supported",
                ),
            )

        new_state = await self._soul.set_plan_mode_from_manual(msg.params.enabled)

        status = StatusUpdate(plan_mode=new_state)
        await self._send_msg(JSONRPCEventMessage(params=status))
        # 持久化到 wire 文件以便回放重建计划模式状态
        await self._soul.wire_file.append_message(status)
        return JSONRPCSuccessResponse(
            id=msg.id,
            result={"status": "ok", "plan_mode": new_state},
        )

    async def _handle_replay(
        self, msg: JSONRPCReplayMessage
    ) -> JSONRPCSuccessResponse | JSONRPCErrorResponse:
        """处理回放请求。

        Args:
            msg: 回放请求消息。

        Returns:
            成功或错误响应。
        """
        if self._is_streaming:
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(
                    code=ErrorCodes.INVALID_STATE, message="An agent turn is already in progress"
                ),
            )

        wire_file = self._soul.wire_file if isinstance(self._soul, NovelSoul) else None

        self._cancel_event = asyncio.Event()
        events = 0
        requests = 0
        try:
            if wire_file is None or not wire_file.path.exists():
                return JSONRPCSuccessResponse(
                    id=msg.id,
                    result={"status": Statuses.FINISHED, "events": 0, "requests": 0},
                )

            async for record in wire_file.iter_records():
                if self._cancel_event.is_set():
                    return JSONRPCSuccessResponse(
                        id=msg.id,
                        result={
                            "status": Statuses.CANCELLED,
                            "events": events,
                            "requests": requests,
                        },
                    )

                try:
                    wire_msg = record.to_wire_message()
                except Exception:
                    logger.exception(
                        "Failed to deserialize wire record for replay: {file}",
                        file=wire_file.path,
                    )
                    continue

                if is_request(wire_msg):
                    await self._send_msg(JSONRPCRequestMessage(id=wire_msg.id, params=wire_msg))
                    requests += 1
                elif is_event(wire_msg):
                    await self._send_msg(JSONRPCEventMessage(params=wire_msg))
                    events += 1
                else:
                    # 对于有效的 WireMessage 不应到达这里，但对损坏数据保留保护。
                    logger.warning(
                        "Skipping non-wire message during replay: {msg}",
                        msg=wire_msg,
                    )

                await asyncio.sleep(0)  # 让出控制权以便处理取消

            if self._cancel_event.is_set():
                return JSONRPCSuccessResponse(
                    id=msg.id,
                    result={
                        "status": Statuses.CANCELLED,
                        "events": events,
                        "requests": requests,
                    },
                )

            return JSONRPCSuccessResponse(
                id=msg.id,
                result={"status": Statuses.FINISHED, "events": events, "requests": requests},
            )
        except Exception:
            logger.exception("Replay failed:")
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(
                    code=ErrorCodes.INTERNAL_ERROR,
                    message="Replay failed",
                ),
            )
        finally:
            self._cancel_event = None

    async def _handle_cancel(
        self, msg: JSONRPCCancelMessage
    ) -> JSONRPCSuccessResponse | JSONRPCErrorResponse:
        """处理取消请求。

        Args:
            msg: 取消请求消息。

        Returns:
            成功或错误响应。
        """
        if not self._is_streaming:
            return JSONRPCErrorResponse(
                id=msg.id,
                error=JSONRPCErrorObject(
                    code=ErrorCodes.INVALID_STATE, message="No agent turn is in progress"
                ),
            )

        assert self._cancel_event is not None
        self._cancel_event.set()
        return JSONRPCSuccessResponse(
            id=msg.id,
            result={},
        )

    async def _handle_response(self, msg: JSONRPCSuccessResponse | JSONRPCErrorResponse) -> None:
        """处理客户端响应消息。

        Args:
            msg: 成功或错误响应消息。
        """
        request = self._pending_requests.pop(msg.id, None)
        if request is None:
            logger.error("No pending request for response id={id}", id=msg.id)
            return

        match request:
            case ApprovalRequest():
                if isinstance(msg, JSONRPCErrorResponse):
                    request.resolve("reject")
                    if self._approval_runtime is not None:
                        self._approval_runtime.resolve(request.id, "reject")
                    return

                try:
                    result = ApprovalResponse.model_validate(msg.result)
                except pydantic.ValidationError as e:
                    logger.error(
                        "Invalid response result for request id={id}: {error}",
                        id=msg.id,
                        error=e,
                    )
                    request.resolve("reject")
                    if self._approval_runtime is not None:
                        self._approval_runtime.resolve(request.id, "reject")
                    return

                if result.request_id != request.id:
                    logger.warning(
                        "Approval response id mismatch: request={request_id}, "
                        "response={response_id}",
                        request_id=request.id,
                        response_id=result.request_id,
                    )
                request.resolve(result.response)
                if self._approval_runtime is not None:
                    self._approval_runtime.resolve(
                        request.id, result.response, feedback=result.feedback
                    )
            case ToolCallRequest():
                if isinstance(msg, JSONRPCErrorResponse):
                    error = msg.error.message
                    request.resolve(
                        ToolError(
                            message=error,
                            brief="External tool error",
                        )
                    )
                    return

                try:
                    tool_result = ToolResult.model_validate(msg.result)
                except pydantic.ValidationError as e:
                    logger.error(
                        "Invalid tool result for request id={id}: {error}",
                        id=msg.id,
                        error=e,
                    )
                    request.resolve(
                        ToolError(
                            message="Invalid tool result payload from client.",
                            brief="Invalid tool result",
                        )
                    )
                    return
                if tool_result.tool_call_id != request.id:
                    logger.warning(
                        "Tool result id mismatch: request={request_id}, result={result_id}",
                        request_id=request.id,
                        result_id=tool_result.tool_call_id,
                    )
                request.resolve(tool_result.return_value)
            case QuestionRequest():
                if isinstance(msg, JSONRPCErrorResponse):
                    request.resolve({})
                    return

                try:
                    result = QuestionResponse.model_validate(msg.result)
                except pydantic.ValidationError as e:
                    logger.error(
                        "Invalid question response for request id={id}: {error}",
                        id=msg.id,
                        error=e,
                    )
                    request.resolve({})
                    return

                if result.request_id != request.id:
                    logger.warning(
                        "Question response id mismatch: request={request_id}, "
                        "response={response_id}",
                        request_id=request.id,
                        response_id=result.request_id,
                    )
                request.resolve(result.answers)
            case HookRequest():
                if isinstance(msg, JSONRPCErrorResponse):
                    request.resolve("allow")
                    return

                try:
                    result = HookResponse.model_validate(msg.result)
                except pydantic.ValidationError as e:
                    logger.error(
                        "Invalid hook response for request id={id}: {error}",
                        id=msg.id,
                        error=e,
                    )
                    request.resolve("allow")
                    return

                if result.request_id != request.id:
                    logger.warning(
                        "Hook response id mismatch: request={request_id}, response={response_id}",
                        request_id=request.id,
                        response_id=result.request_id,
                    )
                request.resolve(result.action, result.reason)

    async def _stream_wire_messages(self, wire: Wire) -> None:
        """流式发送 Wire 消息到客户端。

        Args:
            wire: Wire 通道实例。
        """
        wire_ui = wire.ui_side(merge=False)
        while True:
            msg = await wire_ui.receive()
            match msg:
                case ApprovalRequest():
                    await self._request_approval(msg)
                case ToolCallRequest():
                    await self._request_external_tool(msg)
                case QuestionRequest():
                    await self._request_question(msg)
                case HookRequest():
                    pass  # 通过 hook 引擎回调处理
                case _:
                    await self._send_msg(JSONRPCEventMessage(method="event", params=msg))

    async def _request_approval(self, request: ApprovalRequest) -> None:
        """发送审批请求到客户端。

        Args:
            request: 审批请求对象。

        注意：
            不要在这里 await request.wait()。审批 future 由创建请求的工具
            在 soul 任务内部 await。阻塞 UI 循环会阻止所有后续 Wire 消息
            —— 来自每个并发子 Agent —— 到达 stdout，导致当审批响应丢失时
            （例如没有 WebSocket 连接）产生级联死锁。
        """
        msg_id = request.id  # 使用审批请求 ID 作为消息 ID
        self._pending_requests[msg_id] = request
        await self._send_msg(JSONRPCRequestMessage(id=msg_id, params=request))

    async def _request_external_tool(self, request: ToolCallRequest) -> None:
        """发送外部工具调用请求到客户端。

        Args:
            request: 工具调用请求对象。

        注意：
            与 _request_approval 相同的理由：不要阻塞 UI 循环。
        """
        msg_id = request.id
        self._pending_requests[msg_id] = request
        await self._send_msg(JSONRPCRequestMessage(id=msg_id, params=request))

    async def _request_question(self, request: QuestionRequest) -> None:
        """发送问题请求到客户端。

        Args:
            request: 问题请求对象。
        """
        if not self._client_supports_question:
            # 客户端不支持交互式问题；通知工具以便告知 LLM 使用替代方案。
            request.set_exception(QuestionNotSupported())
            return
        msg_id = request.id
        self._pending_requests[msg_id] = request
        await self._send_msg(JSONRPCRequestMessage(id=msg_id, params=request))
        # 与 _request_approval 相同的理由：不要阻塞 UI 循环。
