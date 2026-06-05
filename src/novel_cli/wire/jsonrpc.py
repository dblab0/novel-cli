"""JSON-RPC 协议消息类型模块。

本模块定义了 JSON-RPC 2.0 协议的消息类型，用于 Wire 服务器与客户端之间的通信。
包含请求、响应、通知等消息类型，以及错误码和状态定义。
"""

from __future__ import annotations

from typing import Any, Literal

from kosong.utils.typing import JsonType
from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_serializer,
)

from novel_cli.wire.serde import serialize_wire_message
from novel_cli.wire.types import (
    ContentPart,
    Event,
    Request,
    is_event,
    is_request,
)


class _MessageBase(BaseModel):
    """JSON-RPC 消息基类。

    Attributes:
        jsonrpc: JSON-RPC 版本号，固定为 "2.0"。
    """

    jsonrpc: Literal["2.0"] = "2.0"

    model_config = ConfigDict(extra="ignore")


class JSONRPCErrorObject(BaseModel):
    """JSON-RPC 错误对象。

    Attributes:
        code: 错误码。
        message: 错误消息。
        data: 附加错误数据（可选）。
    """

    code: int
    message: str
    data: JsonType | None = None


class JSONRPCMessage(_MessageBase):
    """JSON-RPC 通用消息格式，用于消息验证。

    Attributes:
        method: 方法名称（可选）。
        id: 消息标识符（可选）。
        params: 方法参数（可选）。
        result: 响应结果（可选）。
        error: 错误对象（可选）。
    """

    method: str | None = None
    id: str | None = None
    params: JsonType | None = None
    result: JsonType | None = None
    error: JSONRPCErrorObject | None = None

    def method_is_inbound(self) -> bool:
        """检查方法是否为入站方法。

        Returns:
            如果方法是入站方法则返回 True。
        """
        return self.method in JSONRPC_IN_METHODS

    def is_request(self) -> bool:
        """检查消息是否为请求消息。

        Returns:
            如果消息有方法和标识符则返回 True。
        """
        return self.method is not None and self.id is not None

    def is_notification(self) -> bool:
        """检查消息是否为通知消息。

        Returns:
            如果消息有方法但没有标识符则返回 True。
        """
        return self.method is not None and self.id is None

    def is_response(self) -> bool:
        """检查消息是否为响应消息。

        Returns:
            如果消息没有方法但有标识符则返回 True。
        """
        return self.method is None and self.id is not None


class JSONRPCSuccessResponse(_MessageBase):
    """JSON-RPC 成功响应消息。

    Attributes:
        id: 消息标识符。
        result: 响应结果。
    """

    id: str
    result: JsonType


class JSONRPCErrorResponse(_MessageBase):
    """JSON-RPC 错误响应消息。

    Attributes:
        id: 消息标识符。
        error: 错误对象。
    """

    id: str
    error: JSONRPCErrorObject


class JSONRPCErrorResponseNullableID(_MessageBase):
    """JSON-RPC 错误响应消息（标识符可为空）。

    Attributes:
        id: 消息标识符（可为空）。
        error: 错误对象。
    """

    id: str | None
    error: JSONRPCErrorObject


class ClientInfo(BaseModel):
    """客户端信息。

    Attributes:
        name: 客户端名称。
        version: 客户端版本（可选）。
    """

    name: str
    version: str | None = None


class ExternalTool(BaseModel):
    """外部工具定义。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        parameters: 工具参数 schema。
    """

    name: str
    description: str
    parameters: dict[str, JsonType]


class ClientCapabilities(BaseModel):
    """Wire 客户端在初始化时声明的能力。

    Attributes:
        supports_question: 客户端是否支持处理 QuestionRequest 消息。
        supports_plan_mode: 客户端是否支持计划模式（EnterPlanMode / ExitPlanMode）。
    """

    supports_question: bool = False
    supports_plan_mode: bool = False


class WireHookSubscription(BaseModel):
    """来自 Wire 客户端的 Hook 事件订阅。

    Attributes:
        id: 唯一订阅标识符 —— 在 HookRequest 中引用。
        event: 要订阅的事件类型。
        matcher: 正则过滤表达式，空值匹配所有目标。
        timeout: 等待客户端响应的超时时间（秒）。
    """

    id: str
    event: str
    matcher: str = ""
    timeout: int = 30


class JSONRPCInitializeMessage(_MessageBase):
    """JSON-RPC 初始化请求消息。

    Attributes:
        method: 方法名称，固定为 "initialize"。
        id: 消息标识符。
        params: 初始化参数。
    """

    class Params(BaseModel):
        """初始化参数。

        Attributes:
            protocol_version: 协议版本号。
            client: 客户端信息（可选）。
            external_tools: 外部工具列表（可选）。
            hooks: Hook 订阅列表（可选）。
            capabilities: 客户端能力声明（可选）。
        """

        protocol_version: str
        client: ClientInfo | None = None
        external_tools: list[ExternalTool] | None = None
        hooks: list[WireHookSubscription] | None = None
        capabilities: ClientCapabilities | None = None

    method: Literal["initialize"] = "initialize"
    id: str
    params: Params


class JSONRPCPromptMessage(_MessageBase):
    """JSON-RPC 提示消息。

    Attributes:
        method: 方法名称，固定为 "prompt"。
        id: 消息标识符。
        params: 提示参数。
    """

    class Params(BaseModel):
        """提示参数。

        Attributes:
            user_input: 用户输入内容。
        """

        user_input: str | list[ContentPart]

    method: Literal["prompt"] = "prompt"
    id: str
    params: Params

    @model_serializer()
    def _serialize(self) -> dict[str, Any]:
        raise NotImplementedError("Prompt message serialization is not implemented.")


class JSONRPCReplayMessage(_MessageBase):
    """JSON-RPC 回放消息。

    Attributes:
        method: 方法名称，固定为 "replay"。
        id: 消息标识符。
        params: 回放参数（可选）。
    """

    method: Literal["replay"] = "replay"
    id: str
    params: JsonType | None = None


class JSONRPCSteerMessage(_MessageBase):
    """JSON-RPC 转向消息。

    Attributes:
        method: 方法名称，固定为 "steer"。
        id: 消息标识符。
        params: 转向参数。
    """

    class Params(BaseModel):
        """转向参数。

        Attributes:
            user_input: 用户追加的输入内容。
        """

        user_input: str | list[ContentPart]

    method: Literal["steer"] = "steer"
    id: str
    params: Params

    @model_serializer()
    def _serialize(self) -> dict[str, Any]:
        raise NotImplementedError("Steer message serialization is not implemented.")


class _SetPlanModeParams(BaseModel):
    """设置计划模式参数（内部类）。

    Attributes:
        enabled: 是否启用计划模式。
    """

    enabled: bool

    model_config = ConfigDict(extra="ignore")


class JSONRPCSetPlanModeMessage(_MessageBase):
    """JSON-RPC 设置计划模式消息。

    Attributes:
        method: 方法名称，固定为 "set_plan_mode"。
        id: 消息标识符。
        params: 设置计划模式参数。
    """

    method: Literal["set_plan_mode"] = "set_plan_mode"
    id: str
    params: _SetPlanModeParams


class JSONRPCCancelMessage(_MessageBase):
    """JSON-RPC 取消消息。

    Attributes:
        method: 方法名称，固定为 "cancel"。
        id: 消息标识符。
        params: 取消参数（可选）。
    """

    method: Literal["cancel"] = "cancel"
    id: str
    params: JsonType | None = None

    @model_serializer()
    def _serialize(self) -> dict[str, Any]:
        raise NotImplementedError("Cancel message serialization is not implemented.")


class JSONRPCEventMessage(_MessageBase):
    """JSON-RPC 事件消息。

    Attributes:
        method: 方法名称，固定为 "event"。
        params: 事件对象。
    """

    method: Literal["event"] = "event"
    params: Event

    @field_serializer("params")
    def _serialize_params(self, params: Event) -> dict[str, JsonType]:
        return serialize_wire_message(params)

    @field_validator("params", mode="before")
    @classmethod
    def _validate_params(cls, value: Any) -> Event:
        if is_event(value):
            return value
        raise NotImplementedError("Event message deserialization is not implemented.")


class JSONRPCRequestMessage(_MessageBase):
    """JSON-RPC 请求消息。

    Attributes:
        method: 方法名称，固定为 "request"。
        id: 消息标识符。
        params: 请求对象。
    """

    method: Literal["request"] = "request"
    id: str
    params: Request

    @field_serializer("params")
    def _serialize_params(self, params: Request) -> dict[str, JsonType]:
        return serialize_wire_message(params)

    @field_validator("params", mode="before")
    @classmethod
    def _validate_params(cls, value: Any) -> Request:
        if is_request(value):
            return value
        raise NotImplementedError("Request message deserialization is not implemented.")


type JSONRPCInMessage = (
    JSONRPCSuccessResponse
    | JSONRPCErrorResponse
    | JSONRPCInitializeMessage
    | JSONRPCPromptMessage
    | JSONRPCSteerMessage
    | JSONRPCReplayMessage
    | JSONRPCSetPlanModeMessage
    | JSONRPCCancelMessage
)
"""入站 JSON-RPC 消息类型联合。"""

JSONRPCInMessageAdapter = TypeAdapter[JSONRPCInMessage](JSONRPCInMessage)
JSONRPC_IN_METHODS = {"initialize", "prompt", "steer", "replay", "set_plan_mode", "cancel"}
"""入站方法名称集合。"""

type JSONRPCOutMessage = (
    JSONRPCSuccessResponse
    | JSONRPCErrorResponse
    | JSONRPCErrorResponseNullableID
    | JSONRPCEventMessage
    | JSONRPCRequestMessage
)
"""出站 JSON-RPC 消息类型联合。"""

JSONRPC_OUT_METHODS = {"event", "request"}
"""出站方法名称集合。"""


class ErrorCodes:
    """JSON-RPC 错误码定义。

    包含 JSON-RPC 2.0 标准错误码和应用特定错误码。

    Attributes:
        PARSE_ERROR: JSON 解析错误。
        INVALID_REQUEST: 无效请求对象。
        METHOD_NOT_FOUND: 方法不存在。
        INVALID_PARAMS: 无效参数。
        INTERNAL_ERROR: 内部错误。
        INVALID_STATE: 无效状态。
        LLM_NOT_SET: LLM 未设置。
        LLM_NOT_SUPPORTED: LLM 不支持。
        CHAT_PROVIDER_ERROR: 聊天服务错误。
        AUTH_EXPIRED: 认证已过期。
    """

    # JSON-RPC 2.0 标准错误码
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # 应用特定错误码
    INVALID_STATE = -32000
    LLM_NOT_SET = -32001
    LLM_NOT_SUPPORTED = -32002
    CHAT_PROVIDER_ERROR = -32003
    AUTH_EXPIRED = -32004


class Statuses:
    """Agent 运行状态定义。

    Attributes:
        FINISHED: Agent 运行成功完成。
        CANCELLED: Agent 运行被用户取消。
        MAX_STEPS_REACHED: Agent 运行达到最大步数限制。
        STEERED: 转向消息已注入到当前轮转。
    """

    FINISHED = "finished"
    CANCELLED = "cancelled"
    MAX_STEPS_REACHED = "max_steps_reached"
    STEERED = "steered"
