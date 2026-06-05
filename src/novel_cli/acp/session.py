"""ACP 会话管理模块。

本模块实现 ACP 会话的核心功能，包括会话状态管理、消息转换、工具调用处理和权限请求处理等。
"""

from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar

import acp
import streamingjson  # type: ignore[reportMissingTypeStubs]
from kaos import Kaos, reset_current_kaos, set_current_kaos
from kosong.chat_provider import APIStatusError, ChatProviderError

from novel_cli.acp.convert import (
    acp_blocks_to_content_parts,
    display_block_to_acp_content,
    tool_result_to_acp_content,
)
from novel_cli.acp.types import ACPContentBlock
from novel_cli.app import NovelCLI
from novel_cli.soul import LLMNotSet, LLMNotSupported, MaxStepsReached, RunCancelled
from novel_cli.tools import extract_key_argument
from novel_cli.utils.logging import logger
from novel_cli.wire.types import (
    ApprovalRequest,
    ApprovalResponse,
    CompactionBegin,
    CompactionEnd,
    ContentPart,
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
    TurnBegin,
    TurnEnd,
)

# 当前回合 ID 的上下文变量
_current_turn_id = ContextVar[str | None]("current_turn_id", default=None)
# 终端工具调用 ID 集合的上下文变量
_terminal_tool_call_ids = ContextVar[set[str] | None]("terminal_tool_call_ids", default=None)


def get_current_acp_tool_call_id_or_none() -> str | None:
    """获取当前 ACP 工具调用 ID。

    参见 ``_ToolCallState.acp_tool_call_id`` 的说明。

    Returns:
        当前 ACP 工具调用 ID，如果不存在则返回 None。
    """
    from novel_cli.soul.toolset import get_current_tool_call_or_none

    turn_id = _current_turn_id.get()
    if turn_id is None:
        return None
    tool_call = get_current_tool_call_or_none()
    if tool_call is None:
        return None
    return f"{turn_id}/{tool_call.id}"


def register_terminal_tool_call_id(tool_call_id: str) -> None:
    """注册终端工具调用 ID。

    Args:
        tool_call_id: 要注册的工具调用 ID。
    """
    calls = _terminal_tool_call_ids.get()
    if calls is not None:
        calls.add(tool_call_id)


def should_hide_terminal_output(tool_call_id: str) -> bool:
    """判断是否应该隐藏终端输出。

    Args:
        tool_call_id: 工具调用 ID。

    Returns:
        如果该工具调用 ID 已注册且需要隐藏输出，返回 True；否则返回 False。
    """
    calls = _terminal_tool_call_ids.get()
    return calls is not None and tool_call_id in calls


class _ToolCallState:
    """管理单个工具调用的流式更新状态。

    Attributes:
        tool_call: 工具调用对象。
        args: 累积的参数字符串。
        lexer: JSON 词法分析器。
    """

    def __init__(self, tool_call: ToolCall):
        """初始化工具调用状态。

        Args:
            tool_call: 工具调用对象。
        """
        self.tool_call = tool_call
        self.args = tool_call.function.arguments or ""
        self.lexer = streamingjson.Lexer()
        if tool_call.function.arguments is not None:
            self.lexer.append_string(tool_call.function.arguments)

    @property
    def acp_tool_call_id(self) -> str:
        """获取 ACP 工具调用 ID。

        当用户拒绝或取消工具调用时，步骤结果可能不会添加到上下文中。
        在这种情况下，后续步骤可能会发出相同工具调用 ID（LLM 端）的工具调用。
        为了避免 ACP 客户端的混淆，我们通过添加回合 ID 前缀来确保唯一性。

        Returns:
            格式为 "{turn_id}/{tool_call.id}" 的唯一工具调用 ID。
        """
        turn_id = _current_turn_id.get()
        assert turn_id is not None
        return f"{turn_id}/{self.tool_call.id}"

    def append_args_part(self, args_part: str) -> None:
        """追加新的参数片段到累积的参数和词法分析器。

        Args:
            args_part: 要追加的参数片段。
        """
        self.args += args_part
        self.lexer.append_string(args_part)

    def get_title(self) -> str:
        """获取当前标题（如果可用则包含副标题）。

        Returns:
            格式为 "{tool_name}: {subtitle}" 或 "{tool_name}" 的标题。
        """
        tool_name = self.tool_call.function.name
        subtitle = extract_key_argument(self.lexer, tool_name)
        if subtitle:
            return f"{tool_name}: {subtitle}"
        return tool_name


class _TurnState:
    """管理回合状态。

    Attributes:
        id: 回合的唯一 ID。
        tool_calls: 工具调用 ID（LLM 端 ID）到工具调用状态的映射。
        last_tool_call: 最后一个工具调用状态。
        cancel_event: 取消事件信号。
    """

    def __init__(self):
        """初始化回合状态。"""
        self.id = str(uuid.uuid4())
        """回合的唯一 ID。"""
        self.tool_calls: dict[str, _ToolCallState] = {}
        """工具调用 ID（LLM 端 ID）到工具调用状态的映射。"""
        self.last_tool_call: _ToolCallState | None = None
        self.cancel_event = asyncio.Event()


class ACPSession:
    """ACP 会话管理类。

    管理 ACP 会话的生命周期，处理用户提示、消息转换和工具调用等。

    Attributes:
        _id: 会话 ID。
        _cli: Novel CLI 实例。
        _conn: ACP 客户端连接。
        _kaos: kaos 实例。
        _turn_state: 当前回合状态。
    """

    def __init__(
        self,
        id: str,
        cli: NovelCLI,
        acp_conn: acp.Client,
        kaos: Kaos | None = None,
    ) -> None:
        """初始化 ACP 会话。

        Args:
            id: 会话 ID。
            cli: Novel CLI 实例。
            acp_conn: ACP 客户端连接。
            kaos: kaos 实例（可选）。
        """
        self._id = id
        self._cli = cli
        self._conn = acp_conn
        self._kaos = kaos
        self._turn_state: _TurnState | None = None

    @property
    def id(self) -> str:
        """获取 ACP 会话的 ID。

        Returns:
            会话 ID。
        """
        return self._id

    @property
    def cli(self) -> NovelCLI:
        """获取绑定到此 ACP 会话的 Novel CLI 实例。

        Returns:
            Novel CLI 实例。
        """
        return self._cli

    async def prompt(self, prompt: list[ACPContentBlock]) -> acp.PromptResponse:
        """处理用户提示请求。

        Args:
            prompt: ACP 内容块列表，表示用户输入。

        Returns:
            ACP 提示响应对象。

        Raises:
            acp.RequestError: 当 LLM 未设置、不支持或发生 API 错误时抛出。
        """
        user_input = acp_blocks_to_content_parts(prompt)
        self._turn_state = _TurnState()
        token = _current_turn_id.set(self._turn_state.id)
        kaos_token = set_current_kaos(self._kaos) if self._kaos is not None else None
        terminal_tool_calls_token = _terminal_tool_call_ids.set(set())
        try:
            async for msg in self._cli.run(user_input, self._turn_state.cancel_event):
                match msg:
                    case TurnBegin():
                        pass
                    case SteerInput():
                        pass
                    case TurnEnd():
                        pass
                    case StepBegin():
                        pass
                    case StepInterrupted():
                        break
                    case CompactionBegin():
                        pass
                    case CompactionEnd():
                        pass
                    case MCPLoadingBegin():
                        pass
                    case MCPLoadingEnd():
                        pass
                    case StatusUpdate():
                        pass
                    case Notification():
                        await self._send_notification(msg)
                    case ThinkPart(think=think):
                        await self._send_thinking(think)
                    case TextPart(text=text):
                        await self._send_text(text)
                    case ContentPart():
                        logger.warning("Unsupported content part: {part}", part=msg)
                        await self._send_text(f"[{msg.__class__.__name__}]")
                    case ToolCall():
                        await self._send_tool_call(msg)
                    case ToolCallPart():
                        await self._send_tool_call_part(msg)
                    case ToolResult():
                        await self._send_tool_result(msg)
                    case ApprovalResponse():
                        pass
                    case SubagentEvent():
                        pass
                    case PlanDisplay():
                        pass
                    case ApprovalRequest():
                        await self._handle_approval_request(msg)
                    case ToolCallRequest():
                        logger.warning("Unexpected ToolCallRequest in ACP session: {msg}", msg=msg)
                    case QuestionRequest():
                        logger.warning(
                            "QuestionRequest is unsupported in ACP session; resolving empty answer."
                        )
                        msg.resolve({})
                    case _:
                        pass
        except LLMNotSet as e:
            logger.exception("LLM not set:")
            raise acp.RequestError.auth_required() from e
        except LLMNotSupported as e:
            logger.exception("LLM not supported:")
            raise acp.RequestError.internal_error({"error": str(e)}) from e
        except APIStatusError as e:
            logger.exception("LLM API status error:")
            raise acp.RequestError.internal_error({"error": str(e)}) from e
        except ChatProviderError as e:
            logger.exception("LLM provider error:")
            raise acp.RequestError.internal_error({"error": str(e)}) from e
        except MaxStepsReached as e:
            logger.warning("Max steps reached: {n_steps}", n_steps=e.n_steps)
            return acp.PromptResponse(stop_reason="max_turn_requests")
        except RunCancelled:
            logger.info("Prompt cancelled by user")
            return acp.PromptResponse(stop_reason="cancelled")
        except Exception as e:
            logger.exception("Unexpected error during prompt:")
            raise acp.RequestError.internal_error({"error": str(e)}) from e
        finally:
            self._turn_state = None
            if kaos_token is not None:
                reset_current_kaos(kaos_token)
            _terminal_tool_call_ids.reset(terminal_tool_calls_token)
            _current_turn_id.reset(token)
        return acp.PromptResponse(stop_reason="end_turn")

    async def cancel(self) -> None:
        """取消当前正在运行的提示请求。"""
        if self._turn_state is None:
            logger.warning("Cancel requested but no prompt is running")
            return

        self._turn_state.cancel_event.set()

    async def _send_thinking(self, think: str):
        """发送思考内容到客户端。

        Args:
            think: 思考内容文本。
        """
        if not self._id or not self._conn:
            return

        await self._conn.session_update(
            self._id,
            acp.schema.AgentThoughtChunk(
                content=acp.schema.TextContentBlock(type="text", text=think),
                session_update="agent_thought_chunk",
            ),
        )

    async def _send_text(self, text: str):
        """发送文本片段到客户端。

        Args:
            text: 文本内容。
        """
        if not self._id or not self._conn:
            return

        await self._conn.session_update(
            session_id=self._id,
            update=acp.schema.AgentMessageChunk(
                content=acp.schema.TextContentBlock(type="text", text=text),
                session_update="agent_message_chunk",
            ),
        )

    async def _send_notification(self, notification: Notification):
        """发送系统通知到客户端（作为文本片段）。

        Args:
            notification: 通知对象。
        """
        body = notification.body.strip()
        text = f"[Notification] {notification.title}"
        if body:
            text = f"{text}\n{body}"
        await self._send_text(text)

    async def _send_tool_call(self, tool_call: ToolCall):
        """发送工具调用到客户端。

        Args:
            tool_call: 工具调用对象。
        """
        assert self._turn_state is not None
        if not self._id or not self._conn:
            return

        # 创建并存储工具调用状态
        state = _ToolCallState(tool_call)
        self._turn_state.tool_calls[tool_call.id] = state
        self._turn_state.last_tool_call = state

        await self._conn.session_update(
            session_id=self._id,
            update=acp.schema.ToolCallStart(
                session_update="tool_call",
                tool_call_id=state.acp_tool_call_id,
                title=state.get_title(),
                status="in_progress",
                content=[
                    acp.schema.ContentToolCallContent(
                        type="content",
                        content=acp.schema.TextContentBlock(type="text", text=state.args),
                    )
                ],
            ),
        )
        logger.debug("Sent tool call: {name}", name=tool_call.function.name)

    async def _send_tool_call_part(self, part: ToolCallPart):
        """发送工具调用片段（流式参数）。

        Args:
            part: 工具调用片段对象。
        """
        assert self._turn_state is not None
        if (
            not self._id
            or not self._conn
            or not part.arguments_part
            or self._turn_state.last_tool_call is None
        ):
            return

        # 将新的参数片段追加到最后一个工具调用
        self._turn_state.last_tool_call.append_args_part(part.arguments_part)

        # 用新内容和标题更新工具调用
        update = acp.schema.ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=self._turn_state.last_tool_call.acp_tool_call_id,
            title=self._turn_state.last_tool_call.get_title(),
            status="in_progress",
            content=[
                acp.schema.ContentToolCallContent(
                    type="content",
                    content=acp.schema.TextContentBlock(
                        type="text", text=self._turn_state.last_tool_call.args
                    ),
                )
            ],
        )

        await self._conn.session_update(session_id=self._id, update=update)
        logger.debug("Sent tool call update: {delta}", delta=part.arguments_part[:50])

    async def _send_tool_result(self, result: ToolResult):
        """发送工具结果到客户端。

        Args:
            result: 工具结果对象。
        """
        assert self._turn_state is not None
        if not self._id or not self._conn:
            return

        tool_ret = result.return_value

        state = self._turn_state.tool_calls.pop(result.tool_call_id, None)
        if state is None:
            logger.warning("Tool call not found: {id}", id=result.tool_call_id)
            return

        update = acp.schema.ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=state.acp_tool_call_id,
            status="failed" if tool_ret.is_error else "completed",
        )

        contents = (
            []
            if should_hide_terminal_output(state.acp_tool_call_id)
            else tool_result_to_acp_content(tool_ret)
        )
        if contents:
            update.content = contents

        await self._conn.session_update(session_id=self._id, update=update)
        logger.debug("Sent tool result: {id}", id=result.tool_call_id)

        for block in tool_ret.display:
            if isinstance(block, TodoDisplayBlock):
                await self._send_plan_update(block)

    async def _handle_approval_request(self, request: ApprovalRequest):
        """处理权限请求，向客户端发送权限请求。

        Args:
            request: 权限请求对象。
        """
        assert self._turn_state is not None
        if not self._id or not self._conn:
            logger.warning("No session ID, auto-rejecting approval request")
            request.resolve("reject")
            return

        state = self._turn_state.tool_calls.get(request.tool_call_id, None)
        if state is None:
            logger.warning("Tool call not found: {id}", id=request.tool_call_id)
            request.resolve("reject")
            return

        try:
            content: list[
                acp.schema.ContentToolCallContent
                | acp.schema.FileEditToolCallContent
                | acp.schema.TerminalToolCallContent
            ] = []
            if request.display:
                for block in request.display:
                    diff_content = display_block_to_acp_content(block)
                    if diff_content is not None:
                        content.append(diff_content)
            if not content:
                content.append(
                    acp.schema.ContentToolCallContent(
                        type="content",
                        content=acp.schema.TextContentBlock(
                            type="text",
                            text=f"Requesting approval to perform: {request.description}",
                        ),
                    )
                )

            # 发送权限请求并等待响应
            logger.debug("Requesting permission for action: {action}", action=request.action)
            response = await self._conn.request_permission(
                [
                    acp.schema.PermissionOption(
                        option_id="approve",
                        name="Approve once",
                        kind="allow_once",
                    ),
                    acp.schema.PermissionOption(
                        option_id="approve_for_session",
                        name="Approve for this session",
                        kind="allow_always",
                    ),
                    acp.schema.PermissionOption(
                        option_id="reject",
                        name="Reject",
                        kind="reject_once",
                    ),
                ],
                self._id,
                acp.schema.ToolCallUpdate(
                    tool_call_id=state.acp_tool_call_id,
                    title=state.get_title(),
                    content=content,
                ),
            )
            logger.debug("Received permission response: {response}", response=response)

            # 处理结果
            if isinstance(response.outcome, acp.schema.AllowedOutcome):
                # 已选择
                option_id = response.outcome.option_id
                if option_id == "approve":
                    logger.debug("Permission granted for: {action}", action=request.action)
                    request.resolve("approve")
                elif option_id == "approve_for_session":
                    logger.debug("Permission granted for session: {action}", action=request.action)
                    request.resolve("approve_for_session")
                else:
                    logger.debug("Permission denied for: {action}", action=request.action)
                    request.resolve("reject")
            else:
                # 已取消
                logger.debug("Permission request cancelled for: {action}", action=request.action)
                request.resolve("reject")
        except Exception:
            logger.exception("Error handling approval request:")
            # 发生错误时，拒绝请求
            request.resolve("reject")

    async def _send_plan_update(self, block: TodoDisplayBlock) -> None:
        """发送待办事项列表更新作为 ACP 代理计划更新。

        Args:
            block: 待办事项显示块。
        """
        status_map: dict[str, acp.schema.PlanEntryStatus] = {
            "pending": "pending",
            "in progress": "in_progress",
            "in_progress": "in_progress",
            "done": "completed",
            "completed": "completed",
        }
        entries: list[acp.schema.PlanEntry] = [
            acp.schema.PlanEntry(
                content=todo.title,
                priority="medium",
                status=status_map.get(todo.status.lower(), "pending"),
            )
            for todo in block.items
            if todo.title
        ]

        if not entries:
            logger.warning("No valid todo items to send in plan update: {todos}", todos=block.items)
            return

        await self._conn.session_update(
            session_id=self._id,
            update=acp.schema.AgentPlanUpdate(session_update="plan", entries=entries),
        )