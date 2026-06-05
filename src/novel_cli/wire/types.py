"""Wire 消息类型定义模块。

本模块定义了 Wire 协议中使用的所有消息类型，包括：
- 控制流事件（轮转开始/结束、步骤开始/中断等）
- 内容事件（文本、工具调用、工具结果等）
- 请求类型（审批请求、工具调用请求、问题请求等）
- 消息封装和类型判断工具函数
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, TypeGuard, cast

from kosong.chat_provider import TokenUsage
from kosong.message import (
    AudioURLPart,
    ContentPart,
    ImageURLPart,
    TextPart,
    ThinkPart,
    ToolCall,
    ToolCallPart,
    VideoURLPart,
)
from kosong.tooling import (
    BriefDisplayBlock,
    DisplayBlock,
    ToolResult,
    ToolReturnValue,
    UnknownDisplayBlock,
)
from kosong.utils.typing import JsonType
from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from novel_cli.tools.display import (
    BackgroundTaskDisplayBlock,
    DiffDisplayBlock,
    ShellDisplayBlock,
    TodoDisplayBlock,
    TodoDisplayItem,
)
from novel_cli.utils.typing import flatten_union


class TurnBegin(BaseModel):
    """轮转开始事件。

    表示一个新的 Agent 轮转开始。此事件必须在轮转中所有其他事件之前发送。

    Attributes:
        user_input: 用户输入内容，可以是字符串或内容片段列表。
    """

    user_input: str | list[ContentPart]


class SteerInput(BaseModel):
    """转向输入事件。

    表示用户向当前运行的轮转追加后续输入。此事件在当前步骤完成后、
    输入被添加到上下文后、下一个步骤开始前发送。

    Attributes:
        user_input: 用户追加的输入内容。
    """

    user_input: str | list[ContentPart]


class TurnEnd(BaseModel):
    """轮转结束事件。

    表示当前 Agent 轮转结束。此事件必须在轮转中所有其他事件之后发送。
    如果轮转被中断，此事件可能被省略。
    """


class StepBegin(BaseModel):
    """步骤开始事件。

    表示一个新的 Agent 步骤开始。此事件必须在步骤中所有其他事件之前发送。

    Attributes:
        n: 步骤序号。
    """

    n: int


class StepInterrupted(BaseModel):
    """步骤中断事件。

    表示当前步骤被中断，可能由用户干预或错误导致。
    """


class CompactionBegin(BaseModel):
    """压缩开始事件。

    表示压缩操作刚刚开始。此事件必须在步骤期间发送（即在 StepBegin 和
    下一个 StepBegin 或 StepInterrupted 之间），且必须紧随一个 CompactionEnd 事件。
    """


class CompactionEnd(BaseModel):
    """压缩结束事件。

    表示压缩操作刚刚结束。此事件必须紧随 CompactionBegin 事件发送。
    """


class HookTriggered(BaseModel):
    """Hook 触发事件。

    表示一批 Hook 已触发并正在执行。

    Attributes:
        event: Hook 事件类型，如 'PreToolUse'、'Stop'。
        target: Hook 的目标：工具 Hook 为工具名，子 Agent Hook 为 Agent 名等。
        hook_count: 并行运行的匹配 Hook 数量。
    """

    event: str
    target: str = ""
    hook_count: int = 1


class HookResolved(BaseModel):
    """Hook 完成事件。

    表示一批 Hook 已完成执行。

    Attributes:
        event: Hook 事件类型，如 'PreToolUse'、'Stop'。
        target: 与 HookTriggered.target 相同。
        action: 综合决策：如果有任何 Hook 阻止则为 'block'，否则为 'allow'。
        reason: 阻止原因。如果允许则为空。
        duration_ms: 整批 Hook 的墙钟时间（毫秒）。
    """

    event: str
    target: str = ""
    action: Literal["allow", "block"] = "allow"
    reason: str = ""
    duration_ms: int = 0


class MCPLoadingBegin(BaseModel):
    """MCP 工具加载开始事件。

    表示 MCP 工具加载正在进行。
    """


class MCPLoadingEnd(BaseModel):
    """MCP 工具加载结束事件。

    表示 MCP 工具加载已完成。
    """


class MCPServerSnapshot(BaseModel):
    """MCP 服务启动时的服务器快照。

    Attributes:
        name: 服务器名称。
        status: 服务器状态。
        tools: 服务器提供的工具名称列表。
    """

    name: str
    status: Literal["pending", "connecting", "connected", "failed", "unauthorized"]
    tools: tuple[str, ...] = ()


class MCPStatusSnapshot(BaseModel):
    """MCP 启动进度快照。

    Attributes:
        loading: 是否正在加载。
        connected: 已连接的服务器数量。
        total: 服务器总数。
        tools: 工具总数。
        servers: 各服务器快照列表。
    """

    loading: bool
    connected: int
    total: int
    tools: int
    servers: tuple[MCPServerSnapshot, ...] = ()


class StatusUpdate(BaseModel):
    """状态更新事件。

    表示 Soul 当前状态的更新。值为 None 的字段表示与前一个状态相比没有变化。

    Attributes:
        context_usage: 上下文使用率（百分比）。
        context_tokens: 当前上下文中的 token 数量。
        max_context_tokens: 上下文可容纳的最大 token 数量。
        token_usage: 当前步骤的 token 使用统计。
        message_id: 当前步骤的消息 ID。
        plan_mode: 是否激活计划模式（只读模式）。None 表示无变化。
        mcp_status: 当前 MCP 启动快照。None 表示无变化。
    """

    context_usage: float | None = None
    context_tokens: int | None = None
    max_context_tokens: int | None = None
    token_usage: TokenUsage | None = None
    message_id: str | None = None
    plan_mode: bool | None = None
    mcp_status: MCPStatusSnapshot | None = None


class Notification(BaseModel):
    """通用系统通知。

    用于 UI 和客户端消费的通用系统通知。

    Attributes:
        id: 通知标识符。
        category: 通知类别。
        type: 通知类型。
        source_kind: 来源类型。
        source_id: 来源标识符。
        title: 通知标题。
        body: 通知正文。
        severity: 严重程度。
        created_at: 创建时间戳。
        payload: 附加数据。
    """

    id: str
    category: str
    type: str
    source_kind: str
    source_id: str
    title: str
    body: str
    severity: str
    created_at: float
    payload: dict[str, JsonType] = Field(default_factory=dict)


class PlanDisplay(BaseModel):
    """计划显示事件。

    在聊天中以特殊格式内联显示计划内容。

    Attributes:
        content: 计划的完整 markdown 内容。
        file_path: 计划文件路径，用于引用。
    """

    content: str
    file_path: str


class BookList(BaseModel):
    """书籍列表消息，用于前端渲染选择器。

    Attributes:
        books: 可选书籍列表。
        current_book: 当前选中的书籍名称。
    """

    books: list[str]
    current_book: str | None = None


class SubagentEvent(BaseModel):
    """子 Agent 事件。

    来自子 Agent 的事件。

    Attributes:
        parent_tool_call_id: 与此子 Agent 关联的父 Agent 工具调用 ID。
        agent_id: 子 Agent 实例 ID。
        subagent_type: 此实例使用的内置子 Agent 类型。
        event: 来自子 Agent 的事件。
    """

    parent_tool_call_id: str | None = None
    agent_id: str | None = None
    subagent_type: str | None = None
    event: Event
    # TODO: maybe restrict the event types? to exclude approval request, etc.

    @model_validator(mode="before")
    @classmethod
    def _compat_legacy_fields(cls, value: Any) -> Any:
        """兼容旧版字段的验证器。

        Args:
            value: 输入值。

        Returns:
            处理后的值。
        """
        if not isinstance(value, dict):
            return value
        data = dict(cast(dict[str, Any], value))
        if "parent_tool_call_id" not in data and "task_tool_call_id" in data:
            data["parent_tool_call_id"] = data["task_tool_call_id"]
        return data

    @field_serializer("event", when_used="json")
    def _serialize_event(self, event: Event) -> dict[str, Any]:
        """序列化事件字段。

        Args:
            event: 要序列化的事件。

        Returns:
            序列化后的字典。
        """
        envelope = WireMessageEnvelope.from_wire_message(event)
        return envelope.model_dump(mode="json")

    @field_validator("event", mode="before")
    @classmethod
    def _validate_event(cls, value: Any) -> Event:
        """验证事件字段。

        Args:
            value: 输入值。

        Returns:
            验证后的事件对象。

        Raises:
            ValueError: 如果输入不是有效的 Event 类型。
        """
        if is_wire_message(value):
            if is_event(value):
                return value
            raise ValueError("SubagentEvent event must be an Event")

        if not isinstance(value, dict):
            raise ValueError("SubagentEvent event must be a dict")
        event_type = cast(dict[str, Any], value).get("type")
        event_payload = cast(dict[str, Any], value).get("payload")
        envelope = WireMessageEnvelope.model_validate(
            {"type": event_type, "payload": event_payload}
        )
        event = envelope.to_wire_message()
        if not is_event(event):
            raise ValueError("SubagentEvent event must be an Event")
        return event


class ApprovalResponse(BaseModel):
    """审批响应事件。

    表示审批请求已解决。

    Attributes:
        request_id: 已解决的审批请求 ID。
        response: 对审批请求的响应。
        feedback: 拒绝时的可选用户反馈（如给模型的指示）。
    """

    type Kind = Literal["approve", "approve_for_session", "reject"]

    request_id: str
    response: Kind
    feedback: str = ""


class ApprovalRequest(BaseModel):
    """审批请求消息。

    在执行操作前请求用户审批。

    Attributes:
        id: 请求标识符。
        tool_call_id: 工具调用 ID。
        sender: 发送者标识。
        action: 操作名称。
        description: 操作描述。
        source_kind: 来源类型。
        source_id: 来源标识符。
        agent_id: Agent ID（可选）。
        subagent_type: 子 Agent 类型（可选）。
        source_description: 来源描述（可选）。
        display: 显示块列表，默认为空列表以便向后兼容 wire.jsonl 加载。
    """

    id: str
    tool_call_id: str
    sender: str
    action: str
    description: str
    source_kind: Literal["foreground_turn", "background_agent"] | None = None
    source_id: str | None = None
    agent_id: str | None = None
    subagent_type: str | None = None
    source_description: str | None = None
    display: list[DisplayBlock] = Field(default_factory=list[DisplayBlock])

    # 注意：上述字段只是 novel_cli.soul.approval.Request 的副本，
    # 但我们不能直接使用该类，因为要避免 Wire 到 Soul 的依赖。

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._future: asyncio.Future[ApprovalResponse.Kind] | None = None
        self._feedback: str = ""

    def _get_future(self) -> asyncio.Future[ApprovalResponse.Kind]:
        """获取或创建响应 Future。

        Returns:
            响应 Future 对象。
        """
        if self._future is None:
            self._future = asyncio.get_event_loop().create_future()
        return self._future

    async def wait(self) -> ApprovalResponse.Kind:
        """等待请求被解决或取消。

        Returns:
            ApprovalResponse.Kind: 对审批请求的响应。
        """
        return await self._get_future()

    def resolve(self, response: ApprovalResponse.Kind, feedback: str = "") -> None:
        """使用给定响应解决审批请求。

        此操作会使 wait() 方法返回响应。

        Args:
            response: 响应类型。
            feedback: 用户反馈文本。
        """
        self._feedback = feedback
        future = self._get_future()
        if not future.done():
            future.set_result(response)

    @property
    def feedback(self) -> str:
        """用户在拒绝时提供的反馈文本（如果有）。"""
        return self._feedback

    @property
    def resolved(self) -> bool:
        """请求是否已解决。"""
        return self._future is not None and self._future.done()


class QuestionOption(BaseModel):
    """问题的单个选项。

    Attributes:
        label: 选项的显示文本。
        description: 选项含义的解释。
    """

    label: str
    description: str = ""


class QuestionItem(BaseModel):
    """单个问题项。

    Attributes:
        question: 完整的问题文本。
        header: 显示为标签的简短标识（最多 12 个字符）。
        options: 问题的可用选项列表（2-4 个选项）。
        multi_select: 是否允许多选。
        body: 显示在选项上方的可选正文内容（markdown 格式）。
        other_label: 合成的"其他"自由文本选项的自定义标签。空值使用默认值。
        other_description: 合成的"其他"选项的自定义描述。空值使用默认值。
    """

    question: str
    header: str = ""
    options: list[QuestionOption]
    multi_select: bool = False
    body: str = ""
    other_label: str = ""
    other_description: str = ""


class QuestionResponse(BaseModel):
    """问题请求的响应。

    Attributes:
        request_id: 已解决的问题请求 ID。
        answers: 问题文本到选中选项标签的映射。多选答案以逗号分隔。
    """

    request_id: str
    answers: dict[str, str]


class QuestionNotSupported(Exception):
    """当连接的客户端不支持交互式问题时抛出。"""


class QuestionRequest(BaseModel):
    """问题请求消息。

    在执行过程中请求用户回答结构化问题。

    Attributes:
        id: 唯一请求 ID。
        tool_call_id: 发起此问题的工具调用 ID。
        questions: 要向用户提出的问题列表（1-4 个问题）。
    """

    id: str
    tool_call_id: str
    questions: list[QuestionItem]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._future: asyncio.Future[dict[str, str]] | None = None

    def _get_future(self) -> asyncio.Future[dict[str, str]]:
        """获取或创建答案 Future。

        Returns:
            答案 Future 对象。
        """
        if self._future is None:
            self._future = asyncio.get_event_loop().create_future()
        return self._future

    async def wait(self) -> dict[str, str]:
        """等待问题被回答。

        Returns:
            dict[str, str]: 问题文本到答案的映射。
        """
        return await self._get_future()

    def resolve(self, answers: dict[str, str]) -> None:
        """使用给定答案解决问题请求。

        此操作会使 wait() 方法返回答案。

        Args:
            answers: 问题文本到答案的映射。
        """
        future = self._get_future()
        if not future.done():
            future.set_result(answers)

    def set_exception(self, exc: BaseException) -> None:
        """使用异常解决问题请求。

        Args:
            exc: 要设置的异常。
        """
        future = self._get_future()
        if not future.done():
            future.set_exception(exc)

    @property
    def resolved(self) -> bool:
        """问题请求是否已解决。"""
        return self._future is not None and self._future.done()


class ToolCallRequest(BaseModel):
    """工具调用请求消息。

    路由到 Wire 客户端执行的工具调用请求。

    Attributes:
        id: 工具调用 ID。
        name: 要调用的工具名称。
        arguments: 工具调用的参数（JSON 字符串格式）。
    """

    id: str
    name: str
    arguments: str | None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._future: asyncio.Future[ToolReturnValue] | None = None

    def _get_future(self) -> asyncio.Future[ToolReturnValue]:
        """获取或创建结果 Future。

        Returns:
            结果 Future 对象。
        """
        if self._future is None:
            self._future = asyncio.get_event_loop().create_future()
        return self._future

    @staticmethod
    def from_tool_call(tool_call: ToolCall) -> ToolCallRequest:
        """从 ToolCall 创建工具调用请求。

        Args:
            tool_call: 工具调用对象。

        Returns:
            创建的工具调用请求。
        """
        return ToolCallRequest(
            id=tool_call.id,
            name=tool_call.function.name,
            arguments=tool_call.function.arguments,
        )

    async def wait(self) -> ToolReturnValue:
        """等待工具调用被解决或取消。

        Returns:
            ToolReturnValue: 工具执行结果。
        """
        return await self._get_future()

    def resolve(self, result: ToolReturnValue) -> None:
        """使用给定结果解决工具调用。

        此操作会使 wait() 方法返回结果。

        Args:
            result: 工具执行结果。
        """
        future = self._get_future()
        if not future.done():
            future.set_result(result)

    @property
    def resolved(self) -> bool:
        """工具调用是否已解决。"""
        return self._future is not None and self._future.done()


type Event = (
    TurnBegin
    | SteerInput
    | TurnEnd
    | StepBegin
    | StepInterrupted
    | HookTriggered
    | HookResolved
    | CompactionBegin
    | CompactionEnd
    | MCPLoadingBegin
    | MCPLoadingEnd
    | StatusUpdate
    | Notification
    | ContentPart
    | ToolCall
    | ToolCallPart
    | ToolResult
    | ApprovalResponse
    | SubagentEvent
    | PlanDisplay
    | BookList
)
"""事件类型联合，包括控制流事件和内容/工具事件。"""


class HookResponse(BaseModel):
    """Hook 请求的客户端响应。

    Attributes:
        request_id: 正在响应的 HookRequest ID。
        action: 决策：允许操作或阻止操作。
        reason: 阻止原因。如果允许则为空。
    """

    request_id: str
    action: Literal["allow", "block"] = "allow"
    reason: str = ""


class HookRequest(BaseModel):
    """请求 Wire 客户端处理 Hook 事件的请求消息。

    客户端运行其自己的逻辑并以 allow/block 响应。

    Attributes:
        id: 唯一请求 ID。
        subscription_id: 触发此请求的订阅 ID。
        event: Hook 事件类型，如 'PreToolUse'、'Stop'。
        target: 触发 Hook 的目标：工具名、Agent 名等。
        input_data: 完整的事件 payload（与 shell Hook 在 stdin 上获取的相同）。
    """

    type Action = Literal["allow", "block"]

    id: str
    subscription_id: str = ""
    event: str
    target: str = ""
    input_data: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._future: asyncio.Future[tuple[HookRequest.Action, str]] | None = None

    def _get_future(self) -> asyncio.Future[tuple[HookRequest.Action, str]]:
        """获取或创建响应 Future。

        Returns:
            响应 Future 对象。
        """
        if self._future is None:
            self._future = asyncio.get_event_loop().create_future()
        return self._future

    async def wait(self) -> tuple[Action, str]:
        """等待客户端响应。

        Returns:
            tuple[Action, str]: (决策, 原因)。
        """
        return await self._get_future()

    def resolve(self, action: Action, reason: str = "") -> None:
        """使用客户端决策解决请求。

        Args:
            action: 决策类型。
            reason: 原因文本。
        """
        future = self._get_future()
        if not future.done():
            future.set_result((action, reason))

    @property
    def resolved(self) -> bool:
        """请求是否已解决。"""
        return self._future is not None and self._future.done()


type Request = ApprovalRequest | ToolCallRequest | QuestionRequest | HookRequest
"""请求类型联合。请求是期望响应的消息。"""

type WireMessage = Event | Request
"""通过 Wire 发送的消息类型联合。"""


_EVENT_TYPES = cast(tuple[type[Event], ...], flatten_union(Event))
_REQUEST_TYPES = cast(tuple[type[Request], ...], flatten_union(Request))
_WIRE_MESSAGE_TYPES = cast(tuple[type[WireMessage], ...], flatten_union(WireMessage))


def is_event(msg: Any) -> TypeGuard[Event]:
    """检查消息是否为事件类型。

    Args:
        msg: 要检查的消息对象。

    Returns:
        如果消息是事件类型则返回 True。
    """
    return isinstance(msg, _EVENT_TYPES)


def is_request(msg: Any) -> TypeGuard[Request]:
    """检查消息是否为请求类型。

    Args:
        msg: 要检查的消息对象。

    Returns:
        如果消息是请求类型则返回 True。
    """
    return isinstance(msg, _REQUEST_TYPES)


def is_wire_message(msg: Any) -> TypeGuard[WireMessage]:
    """检查消息是否为 WireMessage 类型。

    Args:
        msg: 要检查的消息对象。

    Returns:
        如果消息是 WireMessage 类型则返回 True。
    """
    return isinstance(msg, _WIRE_MESSAGE_TYPES)


_NAME_TO_WIRE_MESSAGE_TYPE: dict[str, type[WireMessage]] = {
    cls.__name__: cls for cls in _WIRE_MESSAGE_TYPES
}
# 用于向后兼容 Wire v1
_NAME_TO_WIRE_MESSAGE_TYPE["ApprovalRequestResolved"] = ApprovalResponse


class WireMessageEnvelope(BaseModel):
    """Wire 消息封装对象。

    用于消息的序列化和反序列化。

    Attributes:
        type: 消息类型名称。
        payload: 消息内容字典。
    """

    type: str
    payload: dict[str, JsonType]

    @classmethod
    def from_wire_message(cls, msg: WireMessage) -> WireMessageEnvelope:
        """从 Wire 消息创建封装对象。

        Args:
            msg: Wire 消息对象。

        Returns:
            创建的消息封装对象。
        """
        typename: str | None = None
        for name, typ in _NAME_TO_WIRE_MESSAGE_TYPE.items():
            if issubclass(type(msg), typ):
                typename = name
                break
        assert typename is not None, f"Unknown wire message type: {type(msg)}"
        return cls(
            type=typename,
            payload=msg.model_dump(mode="json"),
        )

    def to_wire_message(self) -> WireMessage:
        """将封装对象转换回 WireMessage。

        Returns:
            转换后的 Wire 消息对象。

        Raises:
            ValueError: 如果消息类型未知或 payload 无效。
        """
        msg_type = _NAME_TO_WIRE_MESSAGE_TYPE.get(self.type)
        if msg_type is None:
            raise ValueError(f"Unknown wire message type: {self.type}")
        return msg_type.model_validate(self.payload)


__all__ = [
    # `WireMessage` variants
    "TurnBegin",
    "SteerInput",
    "TurnEnd",
    "StepBegin",
    "StepInterrupted",
    "CompactionBegin",
    "CompactionEnd",
    "MCPLoadingBegin",
    "MCPLoadingEnd",
    "StatusUpdate",
    "MCPServerSnapshot",
    "MCPStatusSnapshot",
    "Notification",
    "ContentPart",
    "ToolCall",
    "ToolCallPart",
    "ToolResult",
    "ApprovalResponse",
    "SubagentEvent",
    "PlanDisplay",
    "BookList",
    "ApprovalRequest",
    "ToolCallRequest",
    "QuestionOption",
    "QuestionItem",
    "QuestionResponse",
    "QuestionRequest",
    "QuestionNotSupported",
    # helpers
    "WireMessageEnvelope",
    # `StatusUpdate`-related
    "TokenUsage",
    # `ContentPart` types
    "TextPart",
    "ThinkPart",
    "ImageURLPart",
    "AudioURLPart",
    "VideoURLPart",
    # `ToolResult`-related
    "ToolReturnValue",
    # `DisplayBlock` types
    "DisplayBlock",
    "UnknownDisplayBlock",
    "BriefDisplayBlock",
    "DiffDisplayBlock",
    "TodoDisplayBlock",
    "TodoDisplayItem",
    "ShellDisplayBlock",
    "BackgroundTaskDisplayBlock",
]
