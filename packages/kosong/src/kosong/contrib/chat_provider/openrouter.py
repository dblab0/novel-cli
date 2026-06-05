"""OpenRouter Chat Provider。

通过 OpenAI 兼容 API 调用 OpenRouter 上的模型。
支持 reasoning（思考）功能，通过 extra_body 注入。
"""

import copy
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, Self

import httpx
from openai import AsyncStream, OpenAIError
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
)

from kosong.chat_provider import (
    ChatProvider,
    RetryableChatProvider,
    StreamedMessagePart,
    ThinkingEffort,
    TokenUsage,
)
from kosong.chat_provider.openai_common import (
    convert_error,
    tool_to_openai,
)
from kosong.contrib.chat_provider.openai_legacy import OpenAILegacy
from kosong.message import Message, TextPart, ThinkPart, ToolCall, ToolCallPart
from kosong.tooling import Tool

if TYPE_CHECKING:

    def type_check(openrouter: "OpenRouter"):
        _: ChatProvider = openrouter
        _: RetryableChatProvider = openrouter


def _extract_reasoning_text(reasoning_details: Any) -> str:
    """从 reasoning_details 中提取推理文本。

    OpenRouter 返回的 reasoning_details 可能是:
    - str: 直接作为推理文本
    - list[dict]: 包含 type/text 字段的对象列表（流式响应）
    - dict: 包含 summary 或 text 字段的对象（非流式响应）
    """
    if isinstance(reasoning_details, str):
        return reasoning_details
    if isinstance(reasoning_details, list):
        parts: list[str] = []
        for item in reasoning_details:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # 流式响应中每个 chunk 包含 text 字段
                text = item.get("text", "")
                if text:
                    parts.append(text)
        return "".join(parts)
    if isinstance(reasoning_details, dict):
        # 非流式响应可能包含 summary 或 text
        return reasoning_details.get("text") or reasoning_details.get("summary", "")
    return ""


class OpenRouter(OpenAILegacy):
    """
    使用 OpenRouter API 的 Chat Provider。

    基于 OpenAI 兼容 API，继承 OpenAILegacy 实现。

    >>> chat = OpenRouter(model="google/gemma-4-31b-it:free")
    >>> chat.name
    'openrouter'
    >>> chat.model_name
    'google/gemma-4-31b-it:free'
    """

    name = "openrouter"

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
            api_key = os.environ.get("OPENROUTER_API_KEY")
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
            stream=stream,
            **client_kwargs,
        )
        self._reasoning_enabled: bool = False

    def with_thinking(self, effort: ThinkingEffort) -> Self:
        new_self = copy.copy(self)
        new_self._reasoning_enabled = effort != "off"
        return new_self

    async def generate(  # type: ignore[override]
        self,
        system_prompt: str,
        tools: Sequence[Tool],
        history: Sequence[Message],
    ):
        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self._convert_message(message) for message in history)

        generation_kwargs: dict[str, Any] = {}
        generation_kwargs.update(self._generation_kwargs)

        # 注入 reasoning 参数
        if self._reasoning_enabled:
            generation_kwargs["extra_body"] = {"reasoning": {"enabled": True}}

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=(tool_to_openai(tool) for tool in tools),
                stream=self.stream,
                stream_options={"include_usage": True} if self.stream else None,
                **generation_kwargs,
            )
            return OpenRouterStreamedMessage(response)
        except (OpenAIError, httpx.HTTPError) as e:
            raise convert_error(e) from e


class OpenRouterStreamedMessage:
    def __init__(
        self, response: ChatCompletion | AsyncStream[ChatCompletionChunk]
    ):
        if isinstance(response, ChatCompletion):
            self._iter = self._convert_non_stream_response(response)
        else:
            self._iter = self._convert_stream_response(response)
        self._id: str | None = None
        self._usage: Any = None

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

        # 提取 reasoning_details
        reasoning_details = getattr(message, "reasoning_details", None)
        if reasoning_details:
            reasoning_text = _extract_reasoning_text(reasoning_details)
            if reasoning_text:
                yield ThinkPart(think=reasoning_text)

        if message.content:
            yield TextPart(text=message.content)
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
                if chunk.usage:
                    self._usage = chunk.usage

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # 提取 reasoning_details
                reasoning_details = getattr(delta, "reasoning_details", None)
                if reasoning_details:
                    reasoning_text = _extract_reasoning_text(reasoning_details)
                    if reasoning_text:
                        yield ThinkPart(think=reasoning_text)

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
        except (OpenAIError, httpx.HTTPError) as e:
            raise convert_error(e) from e
