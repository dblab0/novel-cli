import copy
import mimetypes
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, Literal, Self, Unpack, cast

import httpx
from openai import AsyncOpenAI, AsyncStream, BaseModel, OpenAIError, omit
from openai._types import RequestFiles, RequestOptions
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
    VideoURLPart,
)
from kosong.tooling import Tool
from kosong.utils.jsonschema import JsonDict, ensure_property_types

if TYPE_CHECKING:

    def type_check(kimi: "Kimi"):
        _: ChatProvider = kimi
        _: RetryableChatProvider = kimi


class ThinkingConfig(TypedDict, total=False):
    type: Literal["enabled", "disabled"]
    keep: Any
    """Moonshot 特有的 ``thinking.keep`` 开关，用于保留思考内容。
    原样转发给 API；调用者负责选择服务器接受的值（例如 ``"all"``）。"""


class ExtraBody(TypedDict, total=False, extra_items=Any):
    thinking: ThinkingConfig


class Kimi:
    """
    使用 Kimi API 的聊天提供商。

    >>> chat_provider = Kimi(model="kimi-k2-turbo-preview", api_key="sk-1234567890")
    >>> chat_provider.name
    'kimi'
    >>> chat_provider.model_name
    'kimi-k2-turbo-preview'
    >>> chat_provider.with_generation_kwargs(temperature=0)._generation_kwargs
    {'temperature': 0}
    >>> chat_provider._generation_kwargs
    {}
    """

    name = "kimi"

    class GenerationKwargs(TypedDict, total=False):
        """
        详见 https://platform.moonshot.ai/docs/api/chat#request-body。
        """

        max_tokens: int | None
        temperature: float | None
        top_p: float | None
        n: int | None
        presence_penalty: float | None
        frequency_penalty: float | None
        stop: str | list[str] | None
        prompt_cache_key: str | None
        reasoning_effort: str | None
        """旧版思考参数。请使用 `extra_body.thinking` 代替。"""
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
            api_key = os.getenv("KIMI_API_KEY")
        if api_key is None:
            raise ChatProviderError(
                "The api_key client option or the KIMI_API_KEY environment variable is not set"
            )
        if base_url is None:
            base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")

        self.model: str = model
        """要使用的模型名称。"""
        self.stream: bool = stream
        """是否以流式生成响应。"""
        self._api_key: str | None = api_key
        self._base_url: str | None = base_url
        self._client_kwargs: dict[str, Any] = dict(client_kwargs)
        self.client: AsyncOpenAI = create_openai_client(
            api_key=self._api_key,
            base_url=self._base_url,
            client_kwargs=self._client_kwargs,
        )
        """底层的 `AsyncOpenAI` 客户端。"""
        self._generation_kwargs: Kimi.GenerationKwargs = {}

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
    ) -> "KimiStreamedMessage":
        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(_convert_message(message) for message in history)

        generation_kwargs: dict[str, Any] = {
            # default kimi generation kwargs
            "max_tokens": 32000,
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
            return KimiStreamedMessage(response)
        except (OpenAIError, httpx.HTTPError) as e:
            raise convert_error(e) from e

    def on_retryable_error(self, error: BaseException) -> bool:
        old_client = self.client
        # 从实时客户端读取 api_key（而非 self._api_key），以便保留通过
        # client.api_key 应用的 OAuth token 刷新。
        current_api_key = old_client.api_key
        self.client = create_openai_client(
            api_key=current_api_key,
            base_url=self._base_url,
            client_kwargs=self._client_kwargs,
        )
        self._api_key = current_api_key
        close_replaced_openai_client(old_client, client_kwargs=self._client_kwargs)
        return True

    def with_thinking(self, effort: ThinkingEffort) -> Self:
        match effort:
            case "off":
                reasoning_effort = None
            case "low":
                reasoning_effort = "low"
            case "medium":
                reasoning_effort = "medium"
            case "high" | "xhigh" | "max":
                # Kimi's API caps at "high"; xhigh/max are Anthropic-specific.
                reasoning_effort = "high"
        return self.with_generation_kwargs(reasoning_effort=reasoning_effort).with_extra_body(
            {
                "thinking": {
                    "type": "enabled" if effort != "off" else "disabled",
                }
            }
        )

    def with_generation_kwargs(self, **kwargs: Unpack[GenerationKwargs]) -> Self:
        """
        复制聊天提供商，使用给定值更新生成参数。

        Returns:
            Self: 具有更新后生成参数的聊天提供商新实例。
        """
        new_self = copy.copy(self)
        new_self._generation_kwargs = copy.deepcopy(self._generation_kwargs)
        new_self._generation_kwargs.update(kwargs)
        return new_self

    def with_extra_body(self, extra_body: ExtraBody) -> Self:
        """
        复制聊天提供商，更新生成参数中的 extra_body。

        顶层键遵循后写优先语义，但 ``thinking`` 键除外：其子字典会逐字段合并，
        以便后续调用添加 ``thinking.keep`` 时不会覆盖之前 ``with_thinking`` 调用
        设置的 ``thinking.type``。

        Returns:
            Self: 具有更新后 extra_body 的聊天提供商新实例。
        """
        new_self = copy.copy(self)
        new_self._generation_kwargs = copy.deepcopy(self._generation_kwargs)
        old_extra_body = new_self._generation_kwargs.get("extra_body") or {}
        new_extra_body: ExtraBody = {**old_extra_body, **extra_body}
        old_thinking = old_extra_body.get("thinking")
        new_thinking = extra_body.get("thinking")
        if old_thinking is not None and new_thinking is not None:
            new_extra_body["thinking"] = {**old_thinking, **new_thinking}
        new_self._generation_kwargs["extra_body"] = new_extra_body
        return new_self

    @property
    def model_parameters(self) -> dict[str, Any]:
        """
        要使用的模型参数。

        用于追踪/日志记录。
        """

        model_parameters: dict[str, Any] = {"base_url": str(self.client.base_url)}
        model_parameters.update(self._generation_kwargs)
        return model_parameters

    @property
    def files(self) -> "KimiFiles":
        return KimiFiles(self.client)


class KimiFiles:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def upload_video(self, *, data: bytes, mime_type: str) -> VideoURLPart:
        """上传视频到 Kimi 文件 API 并返回视频 URL 内容部分。"""
        if not mime_type.startswith("video/"):
            raise ChatProviderError(f"Expected a video mime type, got {mime_type}")
        url = await self._upload_file(data=data, mime_type=mime_type, purpose="video")
        return VideoURLPart(video_url=VideoURLPart.VideoURL(url=url))

    async def _upload_file(self, *, data: bytes, mime_type: str, purpose: "KimiFilePurpose") -> str:
        filename = _guess_filename(mime_type)
        files: RequestFiles = {"file": (filename, data, mime_type)}
        options: RequestOptions = {"headers": {"Content-Type": "multipart/form-data"}}
        try:
            response: KimiFileObject = await self._client.post(
                "/files",
                cast_to=KimiFileObject,
                body={"purpose": purpose},
                files=files,
                options=options,
            )
        except (OpenAIError, httpx.HTTPError) as e:
            raise convert_error(e) from e
        return f"ms://{response.id}"


class KimiFileObject(BaseModel):
    id: str


type KimiFilePurpose = Literal["video", "image"]


def _guess_filename(mime_type: str) -> str:
    extension = mimetypes.guess_extension(mime_type) or ".bin"
    return f"upload{extension}"


def _convert_message(message: Message) -> ChatCompletionMessageParam:
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
    if (
        message.role == "assistant"
        and message.tool_calls
        and _is_effectively_empty_content_parts(content)
    ):
        # OpenAI 兼容的 API 允许助手工具调用消息省略 `content`，但
        # Kimi-for-Coding 兼容层会拒绝包含空文本部分的内容列表
        # （观察到的现象：`content: [{"type": "text", "text": ""}]` ->
        # 400 "text content is empty"）。当可见内容实际上为空且存在
        # 工具调用时，完全省略 `content` 始终可接受，因此采用此方式。
        dumped_message.pop("content", None)
    if reasoning_content:
        dumped_message["reasoning_content"] = reasoning_content
    return cast(ChatCompletionMessageParam, dumped_message)


def _is_effectively_empty_content_parts(content: Sequence[ContentPart]) -> bool:
    for part in content:
        if not isinstance(part, TextPart):
            return False
        if part.text.strip():
            return False
    return True


def _convert_tool(tool: Tool) -> ChatCompletionToolParam:
    if tool.name.startswith("$"):
        # Kimi 内置函数以 `$` 开头
        return cast(
            ChatCompletionToolParam,
            {
                "type": "builtin_function",
                "function": {
                    "name": tool.name,
                    # 无需设置 description 和 parameters
                },
            },
        )
    converted = tool_to_openai(tool)
    # Moonshot 的 API 会拒绝嵌套属性省略 `type` 的参数 schema（例如某些
    # MCP 服务器暴露的仅枚举属性）。在本地修补 schema，使这些工具在与
    # Kimi 一起工作时无需每个 MCP 服务器作者收紧其 schema。
    function = converted["function"]
    parameters = function.get("parameters")
    if isinstance(parameters, dict):
        normalized = ensure_property_types(cast(JsonDict, parameters))
        function["parameters"] = cast(dict[str, object], normalized)
    return converted


class KimiStreamedMessage:
    """Kimi 聊天提供商的流式消息。"""

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
            if hasattr(self._usage, "cached_tokens"):
                # https://platform.moonshot.cn/docs/api/chat#%E8%BF%94%E5%9B%9E%E5%86%85%E5%AE%B9
                # TODO: 当 Moonshot API 与 OpenAI API 兼容时删除此代码
                cached = getattr(self._usage, "cached_tokens") or 0  # noqa: B009
                other_input -= cached
            elif (
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
        if reasoning_content := getattr(message, "reasoning_content", None):
            assert isinstance(reasoning_content, str)
            yield ThinkPart(think=reasoning_content)
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
                if usage := extract_usage_from_chunk(chunk):
                    self._usage = usage

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # convert thinking content
                if reasoning_content := getattr(delta, "reasoning_content", None):
                    assert isinstance(reasoning_content, str)
                    yield ThinkPart(think=reasoning_content)

                # convert text content
                if delta.content:
                    yield TextPart(text=delta.content)

                # convert tool calls
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
                        # skip empty tool calls
                        pass
        except (OpenAIError, httpx.HTTPError) as e:
            raise convert_error(e) from e


def extract_usage_from_chunk(chunk: ChatCompletionChunk) -> CompletionUsage | None:
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
        chat = Kimi(model="kimi-k2-turbo-preview", stream=False)
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
