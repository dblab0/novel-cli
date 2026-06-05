"""智谱 AI Chat Provider。

使用智谱 AI API 的 Chat Provider 实现，支持 GLM 系列模型的对话补全和深度思考功能。
"""

from __future__ import annotations

import copy
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, Literal, Self, Unpack, cast

import httpx
from openai import AsyncOpenAI, AsyncStream, OpenAIError, omit
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)
from openai.types.completion_usage import CompletionUsage
from typing_extensions import TypedDict

from kosong.chat_provider import (
    ChatProvider,
    ChatProviderError,
    RetryableChatProvider,
    StreamedMessagePart,
    ThinkingEffort,
    TokenUsage,
)
from kosong.chat_provider.openai_common import (
    close_replaced_openai_client,
    convert_error,
    create_openai_client,
    tool_to_openai,
)
from kosong.message import (
    ContentPart,
    Message,
    TextPart,
    ThinkPart,
    ToolCall,
    ToolCallPart,
)
from kosong.tooling import Tool

if TYPE_CHECKING:

    def type_check(zhipu: "Zhipu"):
        _: ChatProvider = zhipu
        _: RetryableChatProvider = zhipu


class ThinkingConfig(TypedDict, total=True):
    """智谱 AI thinking 参数配置。"""

    type: Literal["enabled", "disabled"]
    clear_thinking: bool
    """是否清除历史思考内容。设为 false 以保留 reasoning_content。"""


class ExtraBody(TypedDict, total=False, extra_items=Any):
    """智谱 AI extra_body 参数。"""

    thinking: ThinkingConfig


class Zhipu:
    """
    使用智谱 AI API 的 Chat Provider。

    智谱 AI API 兼容 OpenAI 格式，支持 GLM 系列模型。

    >>> chat_provider = Zhipu(model="glm-5.1", api_key="sk-xxx")
    >>> chat_provider.name
    'zhipu'
    >>> chat_provider.model_name
    'glm-5.1'
    >>> chat_provider.with_generation_kwargs(temperature=0)._generation_kwargs
    {'temperature': 0}
    >>> chat_provider._generation_kwargs
    {}
    """

    name = "zhipu"

    # 智谱 AI OpenAI 兼容 API base URL
    DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"

    class GenerationKwargs(TypedDict, total=False):
        """
        参见 https://docs.bigmodel.cn/api-reference/模型-api/对话补全.md
        """

        max_tokens: int | None
        temperature: float | None
        top_p: float | None
        n: int | None
        presence_penalty: float | None
        frequency_penalty: float | None
        stop: str | list[str] | None
        reasoning_effort: str | None
        """思考努力级别。内部记录用，实际 API 通过 extra_body.thinking 控制。"""
        extra_body: ExtraBody | None

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        stream: bool = True,
        **client_kwargs: Any,
    ):
        if api_key is None:
            api_key = os.getenv("ZHIPU_API_KEY")
        if api_key is None:
            raise ChatProviderError(
                "未设置 api_key 客户端选项或 ZHIPU_API_KEY 环境变量"
            )
        if base_url is None:
            base_url = os.getenv("ZHIPU_BASE_URL", self.DEFAULT_BASE_URL)

        self.model: str = model
        """使用的模型名称。"""
        self.stream: bool = stream
        """是否以流式方式生成响应。"""
        self._api_key: str | None = api_key
        self._base_url: str | None = base_url
        self._client_kwargs: dict[str, Any] = dict(client_kwargs)
        self.client: AsyncOpenAI = create_openai_client(
            api_key=self._api_key,
            base_url=self._base_url,
            client_kwargs=self._client_kwargs,
        )
        """底层的 `AsyncOpenAI` 客户端。"""
        self._generation_kwargs: Zhipu.GenerationKwargs = {}

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def thinking_effort(self) -> ThinkingEffort | None:
        reasoning_effort = self._generation_kwargs.get("reasoning_effort")
        if reasoning_effort is None:
            return None
        match reasoning_effort:
            case "low":
                return "low"
            case "medium":
                return "medium"
            case "high":
                return "high"
            case _:
                return "off"

    async def generate(
        self,
        system_prompt: str,
        tools: Sequence[Tool],
        history: Sequence[Message],
    ) -> "ZhipuStreamedMessage":
        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(_convert_message(message) for message in history)

        generation_kwargs: dict[str, Any] = {
            # 默认的智谱生成参数
            "max_tokens": 128000,
        }
        generation_kwargs.update(self._generation_kwargs)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=(_convert_tool(tool) for tool in tools),
                stream=self.stream,
                stream_options={"include_usage": True} if self.stream else omit,
                **generation_kwargs,
            )
            return ZhipuStreamedMessage(response)
        except (OpenAIError, httpx.HTTPError) as e:
            raise convert_error(e) from e

    def on_retryable_error(self, error: BaseException) -> bool:  # noqa: ARG002
        old_client = self.client
        self.client = create_openai_client(
            api_key=self._api_key,
            base_url=self._base_url,
            client_kwargs=self._client_kwargs,
        )
        close_replaced_openai_client(old_client, client_kwargs=self._client_kwargs)
        return True

    def with_thinking(self, effort: ThinkingEffort) -> Self:
        """
        配置思考努力级别。

        智谱 API 的 thinking 参数不支持 effort 级别，只有 enabled/disabled。
        内部通过 reasoning_effort 记录级别，实际 API 调用使用 extra_body.thinking。

        映射策略：
        - ThinkingEffort.off → {"type": "disabled", "clear_thinking": false}
        - ThinkingEffort.low/medium/high → {"type": "enabled", "clear_thinking": false}

        clear_thinking 始终设为 false，以保留历史对话中的 reasoning_content，
        支持 Preserved Thinking 模式。
        """
        match effort:
            case "off":
                reasoning_effort = None
            case "low":
                reasoning_effort = "low"
            case "medium":
                reasoning_effort = "medium"
            case "high":
                reasoning_effort = "high"
        return self.with_generation_kwargs(reasoning_effort=reasoning_effort).with_extra_body(
            {
                "thinking": {
                    "type": "enabled" if effort != "off" else "disabled",
                    "clear_thinking": False,
                }
            }
        )

    def with_generation_kwargs(self, **kwargs: Unpack[GenerationKwargs]) -> Self:
        """
        复制 Chat Provider，使用给定值更新生成参数。

        Returns:
            Self: 更新了生成参数的新 Chat Provider 实例。
        """
        new_self = copy.copy(self)
        new_self._generation_kwargs = copy.deepcopy(self._generation_kwargs)
        new_self._generation_kwargs.update(kwargs)
        return new_self

    def with_extra_body(self, extra_body: ExtraBody) -> Self:
        """
        复制 Chat Provider，更新生成参数中的 extra_body。

        Returns:
            Self: 更新了 extra_body 的新 Chat Provider 实例。
        """
        new_self = copy.copy(self)
        new_self._generation_kwargs = copy.deepcopy(self._generation_kwargs)
        old_extra_body = new_self._generation_kwargs.get("extra_body") or {}
        new_extra_body: ExtraBody = {**old_extra_body, **extra_body}
        new_self._generation_kwargs["extra_body"] = new_extra_body
        return new_self

    @property
    def model_parameters(self) -> dict[str, Any]:
        """
        使用的模型参数。

        用于追踪/日志目的。
        """
        model_parameters: dict[str, Any] = {"base_url": str(self.client.base_url)}
        model_parameters.update(self._generation_kwargs)
        return model_parameters


def _convert_message(message: Message) -> ChatCompletionMessageParam:
    """将 kosong Message 转换为智谱 API 兼容格式。

    ThinkPart 处理：
    - 发送时: ThinkPart.think → reasoning_content 字段
    """
    message = message.model_copy(deep=True)
    reasoning_content: str = ""
    content: list[ContentPart] = []
    for part in message.content:
        if isinstance(part, ThinkPart):
            reasoning_content += part.think
        else:
            content.append(part)
    message.content = content
    dumped_message = message.model_dump(exclude_none=True)
    if reasoning_content:
        dumped_message["reasoning_content"] = reasoning_content
    return cast(ChatCompletionMessageParam, dumped_message)


def _convert_tool(tool: Tool) -> ChatCompletionToolParam:
    """将 kosong Tool 转换为 OpenAI 兼容格式。

    智谱 API 支持 Function Call，格式与 OpenAI 兼容。
    """
    return tool_to_openai(tool)


class ZhipuStreamedMessage:
    """智谱 AI Chat Provider 的流式消息。"""

    def __init__(self, response: ChatCompletion | AsyncStream[ChatCompletionChunk]):
        if isinstance(response, ChatCompletion):
            self._iter = self._convert_non_stream_response(response)
        else:
            self._iter = self._convert_stream_response(response)
        self._id: str | None = None
        self._usage: CompletionUsage | None = None

    def __aiter__(self) -> AsyncIterator[StreamedMessagePart]:
        return self

    async def __anext__(self) -> StreamedMessagePart:
        return await self._iter.__anext__()

    @property
    def id(self) -> str | None:
        return self._id

    @property
    def usage(self) -> TokenUsage | None:
        if self._usage:
            cached = 0
            other_input = self._usage.prompt_tokens
            if (
                self._usage.prompt_tokens_details
                and self._usage.prompt_tokens_details.cached_tokens
            ):
                cached = self._usage.prompt_tokens_details.cached_tokens
                other_input -= cached
            return TokenUsage(
                input_other=other_input,
                output=self._usage.completion_tokens,
                input_cache_read=cached,
            )
        return None

    async def _convert_non_stream_response(
        self,
        response: ChatCompletion,
    ) -> AsyncIterator[StreamedMessagePart]:
        self._id = response.id
        self._usage = response.usage
        message = response.choices[0].message

        # 转换思考内容
        # 智谱 API 返回的 reasoning_content 是纯文本，不包含签名字段
        # 所以 ThinkPart.encrypted 始终为 None
        if reasoning_content := getattr(message, "reasoning_content", None):
            assert isinstance(reasoning_content, str)
            yield ThinkPart(think=reasoning_content, encrypted=None)

        # 转换文本内容
        if message.content:
            yield TextPart(text=message.content)

        # 转换工具调用
        if message.tool_calls:
            for tool_call in message.tool_calls:
                if isinstance(tool_call, ChatCompletionMessageFunctionToolCall):
                    yield ToolCall(
                        id=tool_call.id or str(uuid.uuid4()),
                        function=ToolCall.FunctionBody(
                            name=tool_call.function.name,
                            arguments=tool_call.function.arguments,
                        ),
                    )

    async def _convert_stream_response(
        self,
        response: AsyncIterator[ChatCompletionChunk],
    ) -> AsyncIterator[StreamedMessagePart]:
        try:
            async for chunk in response:
                if chunk.id:
                    self._id = chunk.id
                if usage := _extract_usage_from_chunk(chunk):
                    self._usage = usage

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # 转换思考内容
                if reasoning_content := getattr(delta, "reasoning_content", None):
                    assert isinstance(reasoning_content, str)
                    yield ThinkPart(think=reasoning_content, encrypted=None)

                # 转换文本内容
                if delta.content:
                    yield TextPart(text=delta.content)

                # 转换工具调用
                for tool_call in delta.tool_calls or []:
                    if not tool_call.function:
                        continue

                    if tool_call.function.name:
                        yield ToolCall(
                            id=tool_call.id or str(uuid.uuid4()),
                            function=ToolCall.FunctionBody(
                                name=tool_call.function.name,
                                arguments=tool_call.function.arguments,
                            ),
                        )
                    elif tool_call.function.arguments:
                        yield ToolCallPart(
                            arguments_part=tool_call.function.arguments,
                        )
                    else:
                        # 跳过空工具调用
                        pass
        except (OpenAIError, httpx.HTTPError) as e:
            raise convert_error(e) from e


def _extract_usage_from_chunk(chunk: ChatCompletionChunk) -> CompletionUsage | None:
    """从 chunk 中提取 usage 信息。"""
    if chunk.usage:
        return chunk.usage
    if not chunk.choices:
        return None
    choice_dump: dict[str, object] = chunk.choices[0].model_dump()
    raw_usage = choice_dump.get("usage")
    if isinstance(raw_usage, CompletionUsage):
        return raw_usage
    if isinstance(raw_usage, dict):
        return CompletionUsage.model_validate(raw_usage)
    return None


if __name__ == "__main__":

    async def _dev_main():
        chat = Zhipu(model="glm-5.1", stream=False)
        system_prompt = ""
        history = [
            Message(role="user", content="Hello, who is Confucius?"),
        ]
        stream = await chat.with_generation_kwargs(
            temperature=0,
            max_tokens=1000,
        ).generate(system_prompt, [], history)
        async for part in stream:
            print(part.model_dump(exclude_none=True))
        print("id:", stream.id)
        print("usage:", stream.usage)

    import asyncio

    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(_dev_main())
