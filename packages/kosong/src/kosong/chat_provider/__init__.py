from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel

from kosong.message import ContentPart, Message, ToolCall, ToolCallPart
from kosong.tooling import Tool

if TYPE_CHECKING:
    import httpx


@runtime_checkable
class ChatProvider(Protocol):
    """聊天提供商的接口。"""

    name: str
    """
    聊天提供商的名称。
    """

    @property
    def model_name(self) -> str:
        """
        要使用的模型名称。
        """
        ...

    @property
    def thinking_effort(self) -> ThinkingEffort | None:
        """
        当前的思考努力级别。如果未显式设置，返回 None。
        """
        ...

    async def generate(
        self,
        system_prompt: str,
        tools: Sequence[Tool],
        history: Sequence[Message],
    ) -> StreamedMessage:
        """
        基于给定的系统提示、工具和历史记录生成新消息。

        Raises:
            APIConnectionError: 如果 API 连接失败。
            APITimeoutError: 如果 API 请求超时。
            APIStatusError: 如果 API 返回 4xx 或 5xx 状态码。
            ChatProviderError: 如果发生任何其他已识别的聊天提供商错误。
        """
        ...

    def with_thinking(self, effort: ThinkingEffort) -> Self:
        """
        返回配置了给定思考努力级别的 self 副本。
        如果聊天提供商不支持思考，则简单地返回 self 副本。
        """
        ...


@runtime_checkable
class RetryableChatProvider(Protocol):
    """可选接口，用于可从可重试传输错误中恢复的提供商。"""

    def on_retryable_error(self, error: BaseException) -> bool:
        """
        尝试在可重试错误后恢复提供商传输状态。

        Returns:
            bool: 是否执行了恢复操作。
        """
        ...


type StreamedMessagePart = ContentPart | ToolCall | ToolCallPart


@runtime_checkable
class StreamedMessage(Protocol):
    """流式消息的接口。"""

    def __aiter__(self) -> AsyncIterator[StreamedMessagePart]:
        """从流创建异步迭代器。"""
        ...

    @property
    def id(self) -> str | None:
        """流式消息的 ID。"""
        ...

    @property
    def usage(self) -> TokenUsage | None:
        """流式消息的 token 使用量。"""
        ...


class TokenUsage(BaseModel):
    """Token 使用量统计。"""

    input_other: int
    """输入 token，不包括 `input_cache_read` 和 `input_cache_creation`。"""
    output: int
    """总输出 token。"""
    input_cache_read: int = 0
    """缓存的输入 token。"""
    input_cache_creation: int = 0
    """用于缓存创建的输入 token。目前仅 Anthropic API 支持此功能。"""

    @property
    def total(self) -> int:
        """使用的总 token，包括输入和输出 token。"""
        return self.input + self.output

    @property
    def input(self) -> int:
        """总输入 token，包括缓存和未缓存的 token。"""
        return self.input_other + self.input_cache_read + self.input_cache_creation


type ThinkingEffort = Literal["off", "low", "medium", "high", "xhigh", "max"]
"""思考的努力级别。

对高于 ``high`` 级别的支持因提供商而异：

- **Anthropic**: ``xhigh`` 仅在 Claude Opus 4.7 上被接受；``max`` 在
  Mythos、Opus 4.7/4.6 和 Sonnet 4.6 上被接受。不支持的级别会被
  限制到 ``high``。
- **OpenAI**: ``xhigh`` 原生被 ``gpt-5.1-codex-max`` 之后的推理模型接受，
  并原样传递。``max`` 是 Anthropic 特有的，被限制为 ``xhigh``（OpenAI 的上限）。
- **Kimi / Gemini**: ``xhigh`` 和 ``max`` 被限制为 ``high``（无原生支持）。
"""


class ChatProviderError(Exception):
    """聊天提供商引发的错误。"""

    def __init__(self, message: str):
        super().__init__(message)


class APIConnectionError(ChatProviderError):
    """API 连接失败时引发的错误。"""


class APITimeoutError(ChatProviderError):
    """API 请求超时时引发的错误。"""


class APIStatusError(ChatProviderError):
    """API 返回 4xx 或 5xx 状态码时引发的错误。"""

    status_code: int
    request_id: str | None

    def __init__(self, status_code: int, message: str, *, request_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class APIEmptyResponseError(ChatProviderError):
    """API 返回空响应时引发的错误。"""


def convert_httpx_error(error: httpx.HTTPError) -> ChatProviderError:
    """将 httpx 传输错误转换为相应的 ChatProviderError。

    这是所有聊天提供商的共享工具。SDK 特定的异常（例如 AnthropicError、
    OpenAIError）应由每个提供商自己的转换逻辑处理；只有泄漏出来的
    原始 httpx 异常（通常在流式传输期间）应该路由到这里。
    """
    import httpx

    if isinstance(error, httpx.TimeoutException):
        return APITimeoutError(str(error))
    if isinstance(error, (httpx.NetworkError, httpx.RemoteProtocolError)):
        return APIConnectionError(str(error))
    if isinstance(error, httpx.HTTPStatusError):
        req_id = error.response.headers.get("x-request-id")
        return APIStatusError(error.response.status_code, str(error), request_id=req_id)
    return ChatProviderError(f"HTTP error: {error}")
