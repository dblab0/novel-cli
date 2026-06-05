try:
    import anthropic as _  # noqa: F401
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Anthropic 支持需要可选依赖 'anthropic'。"
        '请使用 `pip install "kosong[contrib]"` 安装。'
    ) from exc

import copy
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Self, TypedDict, Unpack, cast

import httpx
from anthropic import (
    AnthropicError,
    AsyncAnthropic,
    AsyncStream,
    omit,
)
from anthropic import (
    APIConnectionError as AnthropicAPIConnectionError,
)
from anthropic import (
    APIStatusError as AnthropicAPIStatusError,
)
from anthropic import (
    APITimeoutError as AnthropicAPITimeoutError,
)
from anthropic import (
    AuthenticationError as AnthropicAuthenticationError,
)
from anthropic import (
    PermissionDeniedError as AnthropicPermissionDeniedError,
)
from anthropic import (
    RateLimitError as AnthropicRateLimitError,
)
from anthropic.lib.streaming import MessageStopEvent
from anthropic.types import (
    Base64ImageSourceParam,
    CacheControlEphemeralParam,
    ContentBlockParam,
    ImageBlockParam,
    MessageDeltaEvent,
    MessageDeltaUsage,
    MessageParam,
    MessageStartEvent,
    MetadataParam,
    OutputConfigParam,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawMessageStreamEvent,
    TextBlockParam,
    ThinkingBlockParam,
    ThinkingConfigParam,
    ToolChoiceParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
    URLImageSourceParam,
    Usage,
)
from anthropic.types import (
    Message as AnthropicMessage,
)
from anthropic.types.tool_result_block_param import Content as ToolResultContent

from kosong.chat_provider import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ChatProvider,
    ChatProviderError,
    StreamedMessagePart,
    ThinkingEffort,
    TokenUsage,
    convert_httpx_error,
)
from kosong.contrib.chat_provider.common import ToolMessageConversion
from kosong.message import (
    ContentPart,
    ImageURLPart,
    Message,
    TextPart,
    ThinkPart,
    ToolCall,
    ToolCallPart,
)
from kosong.tooling import Tool

if TYPE_CHECKING:

    def type_check(anthropic: "Anthropic"):
        _: ChatProvider = anthropic


type MessagePayload = tuple[str | None, list[MessageParam]]

type BetaFeatures = Literal["interleaved-thinking-2025-05-14"]


# 支持自适应思考但不在其标识符中公开 major.minor 版本的模型
#（例如 Claude Mythos Preview）。
_ADAPTIVE_MARKERS_NO_VERSION: tuple[str, ...] = ("mythos",)

# 匹配模型标识符内 Claude 系列的 major.minor 版本。
# `\d{1,2}(?!\d)` 防止像 `sonnet-4-20250514` 这样的日期后缀被误读为
# minor=20：`20` 后面会跟着另一个数字，因此否定前瞻失败且正则表达式
# 根本不匹配。
_FAMILY_VERSION_RE = re.compile(r"(?:opus|sonnet|haiku)[.-](\d+)[.-](\d{1,2})(?!\d)")

# 自适应思考随 Opus 4.6 / Sonnet 4.6 引入。
_ADAPTIVE_MIN_VERSION: tuple[int, int] = (4, 6)


def _supports_adaptive_thinking(model: str) -> bool:
    """给定模型 ID 是否接受 `thinking: {type: "adaptive"}`。

    策略：针对无版本模型的显式标记（例如 Mythos），以及对 Claude 系列的
    严格正则表达式，可外推到未知的未来版本（>= 4.6）而无需代码更改。
    """
    m = model.lower()
    if any(marker in m for marker in _ADAPTIVE_MARKERS_NO_VERSION):
        return True
    if match := _FAMILY_VERSION_RE.search(m):
        major, minor = int(match.group(1)), int(match.group(2))
        return (major, minor) >= _ADAPTIVE_MIN_VERSION
    return False


def _is_opus_4_7(model: str) -> bool:
    """Opus 4.7 专门支持 ``xhigh`` 努力级别。

    文档明确将 ``xhigh`` 支持列举为"可在 Claude Opus 4.7 上使用"——
    而不是"4.7 及更高版本"。我们保持检查精确，以便未来静默丢弃 xhigh
    的 Opus 4.8 不会开始返回 400。未来版本会回退到 4.6 系列努力集
    （仍然覆盖 ``max``），直到更新此表。
    """
    m = model.lower()
    if match := re.search(r"opus[.-](\d+)[.-](\d{1,2})(?!\d)", m):
        major, minor = int(match.group(1)), int(match.group(2))
        return (major, minor) == (4, 7)
    return False


def _supported_efforts(model: str) -> frozenset["ThinkingEffort"]:
    """给定模型接受的 ``output_config.effort`` 努力级别。

    根据 Anthropic 文档：
      - xhigh: 仅 Opus 4.7
      - max:   Mythos、Opus 4.7、Opus 4.6、Sonnet 4.6（及未来的自适应模型）
      - low/medium/high: 所有支持努力的模型（包括 Opus 4.5+）
    """
    if _is_opus_4_7(model):
        return frozenset({"low", "medium", "high", "xhigh", "max"})
    if _supports_adaptive_thinking(model):
        # 4.6 family / Mythos / future adaptive models: support max but not xhigh
        return frozenset({"low", "medium", "high", "max"})
    # Pre-4.6 models: capped at high
    return frozenset({"low", "medium", "high"})


def _clamp_effort(effort: "ThinkingEffort", model: str) -> "ThinkingEffort":
    """将努力级别限制为模型支持的最高级别。

    模型不支持的任何级别都会回退到 ``high``——这是普遍可用的上限。
    ``off`` 保持不变传递，因为它代表"禁用思考"而非努力等级。
    """
    if effort == "off":
        return effort
    if effort in _supported_efforts(model):
        return effort
    return "high"


def _supports_effort_param(model: str) -> bool:
    """模型是否完全接受 ``output_config.effort``。

    根据 Anthropic 的努力文档，该参数在 Claude Mythos Preview、Claude
    Opus 4.7、Claude Opus 4.6、Claude Sonnet 4.6 和 Claude Opus 4.5 上
    得到明确支持。支持自适应的模型都通过自适应路径支持它。对于旧版
    （手动思考）路径，仅明确列出了 Opus 4.5。

    我们根据此谓词来限制 ``output_config`` 的发送，以避免向会以 400
    拒绝的模型发送努力（Claude 3.x，以及保守起见，不在显式列表中的
    Sonnet 4 / Sonnet 4.5 / Haiku 4.5）。这里的假阴性意味着"未发送努力，
    不会从支持努力之前的行为回退"；假阳性则意味着"API 400 错误"，
    因此我们宁可保守。
    """
    if _supports_adaptive_thinking(model):
        return True
    # Opus 4.5 is the only legacy (non-adaptive) model that Anthropic docs
    # explicitly confirm supports the effort parameter.
    m = model.lower()
    return "opus-4-5" in m or "opus-4.5" in m


class Anthropic:
    """
    由 Anthropic 的 Messages API 支持的聊天提供商。
    """

    name = "anthropic"

    class GenerationKwargs(TypedDict, total=False):
        max_tokens: int | None
        temperature: float | None
        top_k: int | None
        top_p: float | None
        # 例如，{"type": "adaptive", "display": "summarized"}
        # 或   {"type": "enabled", "budget_tokens": 1024}
        thinking: ThinkingConfigParam | None
        # 例如，{"effort": "high"} — 适用于所有输出 token 的软指导。
        # 用于自适应思考请求，以及在旧版请求中仅当模型位于 Anthropic
        # 的明确支持努力的列表时（见 ``_supports_effort_param``）。
        output_config: OutputConfigParam | None
        # 例如，{"type": "auto", "disable_parallel_tool_use": True}
        tool_choice: ToolChoiceParam | None

        beta_features: list[BetaFeatures] | None
        extra_headers: Mapping[str, str] | None

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        stream: bool = True,
        # 应对工具结果应用哪个处理
        tool_message_conversion: ToolMessageConversion | None = None,
        # 必须提供 max_tokens。可被 .with_generation_kwargs() 覆盖
        default_max_tokens: int,
        metadata: MetadataParam | None = None,
        **client_kwargs: Any,
    ):
        self._model = model
        self._stream = stream
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url, **client_kwargs)
        self._tool_message_conversion: ToolMessageConversion | None = tool_message_conversion
        self._metadata = metadata
        self._generation_kwargs: Anthropic.GenerationKwargs = {
            "max_tokens": default_max_tokens,
            "beta_features": ["interleaved-thinking-2025-05-14"],
        }

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def thinking_effort(self) -> "ThinkingEffort | None":
        thinking_config = self._generation_kwargs.get("thinking")
        if thinking_config is None:
            return None
        if thinking_config["type"] == "disabled":
            return "off"
        if thinking_config["type"] == "adaptive":
            output_config = self._generation_kwargs.get("output_config") or {}
            effort = output_config.get("effort")
            if effort in ("low", "medium", "high", "xhigh", "max"):
                return effort
            return "high"
        budget = thinking_config["budget_tokens"]
        if budget <= 1024:
            return "low"
        if budget <= 4096:
            return "medium"
        return "high"

    async def generate(
        self,
        system_prompt: str,
        tools: Sequence[Tool],
        history: Sequence[Message],
    ) -> "AnthropicStreamedMessage":
        # https://docs.claude.com/en/api/messages#body-messages
        # Anthropic API 不支持系统角色，仅支持系统提示。
        system = (
            [
                TextBlockParam(
                    text=system_prompt,
                    type="text",
                    cache_control=CacheControlEphemeralParam(type="ephemeral"),
                )
            ]
            if system_prompt
            else omit
        )
        messages: list[MessageParam] = []
        for message in history:
            converted = self._convert_message(message)
            # Per Anthropic spec, tool_result blocks for the same assistant turn
            # must live in a single user message. Internal Messages model one
            # tool call per entry, so merge consecutive tool-result-only user
            # messages here. Strict-compat backends (e.g. DeepSeek /anthropic)
            # 400 on the split form, and the official backend silently teaches
            # the model to stop calling tools in parallel.
            if (
                messages
                and converted["role"] == "user"
                and messages[-1]["role"] == "user"
                and _is_tool_result_only(messages[-1]["content"])
                and _is_tool_result_only(converted["content"])
            ):
                prev_content = cast(list[ContentBlockParam], messages[-1]["content"])
                new_content = cast(list[ContentBlockParam], converted["content"])
                messages[-1]["content"] = [*prev_content, *new_content]
            else:
                messages.append(converted)
        if messages:
            last_message = messages[-1]
            last_content = last_message["content"]

            # inject cache control in the last content.
            # https://docs.claude.com/en/docs/build-with-claude/prompt-caching
            if isinstance(last_content, list) and last_content:
                content_blocks = cast(list[ContentBlockParam], last_content)
                last_block = content_blocks[-1]
                match last_block["type"]:
                    case (
                        "text"
                        | "image"
                        | "document"
                        | "search_result"
                        | "tool_use"
                        | "tool_result"
                        | "server_tool_use"
                        | "web_search_tool_result"
                    ):
                        last_block["cache_control"] = CacheControlEphemeralParam(type="ephemeral")
                    case "thinking" | "redacted_thinking":
                        pass
        generation_kwargs: dict[str, Any] = {}
        generation_kwargs.update(self._generation_kwargs)
        betas = generation_kwargs.pop("beta_features", [])
        extra_headers = {
            **{"anthropic-beta": ",".join(str(e) for e in betas)},
            **(generation_kwargs.pop("extra_headers", {})),
        }

        tools_ = [_convert_tool(tool) for tool in tools]
        if tools:
            tools_[-1]["cache_control"] = CacheControlEphemeralParam(type="ephemeral")
        try:
            response = await self._client.messages.create(
                model=self._model,
                messages=messages,
                system=system,
                tools=tools_,
                stream=self._stream,
                extra_headers=extra_headers,
                metadata=self._metadata if self._metadata is not None else omit,
                **generation_kwargs,
            )
            return AnthropicStreamedMessage(response)
        except (AnthropicError, httpx.HTTPError) as e:
            raise _convert_error(e) from e

    def with_thinking(self, effort: "ThinkingEffort") -> Self:
        if effort == "off":
            new = self.with_generation_kwargs(thinking={"type": "disabled"})
            # Clear any stale output_config from a prior adaptive configuration.
            new._generation_kwargs.pop("output_config", None)
            return new

        # Clamp to whatever the model actually accepts. xhigh/max fall back
        # to high on models that don't support them; low/medium/high pass
        # through; max passes through on 4.6-family and newer.
        effective = _clamp_effort(effort, self._model)
        # SDK 0.78 OutputConfigParam TypedDict lists only low/medium/high/max.
        # `xhigh` is valid on Opus 4.7 per the API docs but not yet typed.
        output_config: OutputConfigParam = {"effort": effective}  # type: ignore[typeddict-item]

        if _supports_adaptive_thinking(self._model):
            # Opus 4.6+ / Sonnet 4.6+ / Mythos: adaptive thinking.
            # `display: "summarized"` is required on Opus 4.7+ (where the default
            # flipped to "omitted") and is a no-op on 4.6. Setting it
            # unconditionally keeps thinking content visible across versions.
            # SDK 0.78 TypedDict doesn't model `display` yet — thus the ignore.
            thinking_config: ThinkingConfigParam = {
                "type": "adaptive",
                "display": "summarized",
            }  # type: ignore[typeddict-item]
            new = self.with_generation_kwargs(
                thinking=thinking_config,
                output_config=output_config,
            )
            # Adaptive mode auto-enables interleaved thinking, so the beta
            # header is redundant. Drop it if still present from construction.
            if (
                beta_features := new._generation_kwargs.get("beta_features")
            ) and "interleaved-thinking-2025-05-14" in beta_features:
                beta_features.remove("interleaved-thinking-2025-05-14")
            return new

        # Pre-4.6 models: legacy budget-based thinking. After clamping,
        # `effective` is guaranteed to be one of low/medium/high here.
        # Only models that Anthropic's docs explicitly list as supporting the
        # effort parameter (e.g. Opus 4.5) get `output_config` emitted; other
        # pre-4.6 models (Sonnet 4, Sonnet 4.5, Haiku 4.5, Claude 3.x) omit it
        # to avoid 400 validation errors on models that don't accept it.
        budgets: dict[str, int] = {"low": 1024, "medium": 4096, "high": 32_000}
        kwargs: dict[str, Any] = {
            "thinking": {"type": "enabled", "budget_tokens": budgets[effective]},
        }
        if _supports_effort_param(self._model):
            kwargs["output_config"] = output_config
        return self.with_generation_kwargs(**kwargs)

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

    @property
    def model_parameters(self) -> dict[str, Any]:
        """
        要使用的模型参数。

        用于追踪/日志记录。
        """

        model_parameters: dict[str, Any] = {"base_url": str(self._client.base_url)}
        model_parameters.update(self._generation_kwargs)
        return model_parameters

    def _convert_message(self, message: Message) -> MessageParam:
        """将单个内部消息转换为 Anthropic 线路格式。"""
        role = message.role

        if role == "system":
            # Anthropic 不支持对话中的系统消息。
            # 我们将其映射为特殊的用户消息。
            return MessageParam(
                role="user",
                content=[
                    TextBlockParam(
                        type="text", text=f"<system>{message.extract_text(sep='\n')}</system>"
                    )
                ],
            )
        elif role == "tool":
            if message.tool_call_id is None:
                raise ChatProviderError("Tool message missing `tool_call_id`.")
            if self._tool_message_conversion == "extract_text":
                content = message.extract_text(sep="\n")
            else:
                content = message.content
            block = _tool_result_message_to_block(message.tool_call_id, content)
            return MessageParam(role="user", content=[block])

        assert role in ("user", "assistant")
        blocks: list[ContentBlockParam] = []
        for part in message.content:
            if isinstance(part, TextPart):
                blocks.append(TextBlockParam(type="text", text=part.text))
            elif isinstance(part, ImageURLPart):
                blocks.append(_image_url_part_to_anthropic(part))
            elif isinstance(part, ThinkPart):
                if part.encrypted is None:
                    # 缺少签名，移除此思考块。
                    continue
                else:
                    blocks.append(
                        ThinkingBlockParam(
                            type="thinking", thinking=part.think, signature=part.encrypted
                        )
                    )
            else:
                continue
        for tool_call in message.tool_calls or []:
            if tool_call.function.arguments:
                try:
                    parsed_arguments = json.loads(tool_call.function.arguments, strict=False)
                except json.JSONDecodeError as exc:  # pragma: no cover - defensive guard
                    raise ChatProviderError("Tool call arguments must be valid JSON.") from exc
                if not isinstance(parsed_arguments, dict):
                    raise ChatProviderError("Tool call arguments must be a JSON object.")
                tool_input = cast(dict[str, object], parsed_arguments)
            else:
                tool_input = {}
            blocks.append(
                ToolUseBlockParam(
                    type="tool_use",
                    id=tool_call.id,
                    name=tool_call.function.name,
                    input=tool_input,
                )
            )
        return MessageParam(role=role, content=blocks)


class AnthropicStreamedMessage:
    def __init__(self, response: AnthropicMessage | AsyncStream[RawMessageStreamEvent]):
        if isinstance(response, AnthropicMessage):
            self._iter = self._convert_non_stream_response(response)
        else:
            self._iter = self._convert_stream_response(response)
        self._id: str | None = None
        self._usage = Usage(input_tokens=0, output_tokens=0)

    def __aiter__(self) -> AsyncIterator[StreamedMessagePart]:
        return self

    async def __anext__(self) -> StreamedMessagePart:
        return await self._iter.__anext__()

    @property
    def id(self) -> str | None:
        return self._id

    @property
    def usage(self) -> TokenUsage | None:
        # https://docs.claude.com/en/docs/build-with-claude/prompt-caching#tracking-cache-performance
        return TokenUsage(
            # Note: in some Anthropic-compatible APIs, input_tokens can be None
            input_other=self._usage.input_tokens or 0,
            output=self._usage.output_tokens,
            input_cache_read=self._usage.cache_read_input_tokens or 0,
            input_cache_creation=self._usage.cache_creation_input_tokens or 0,
        )

    def _update_usage(self, delta_usage: MessageDeltaUsage) -> None:
        if delta_usage.cache_creation_input_tokens is not None:
            self._usage.cache_creation_input_tokens = delta_usage.cache_creation_input_tokens
        if delta_usage.cache_read_input_tokens is not None:
            self._usage.cache_read_input_tokens = delta_usage.cache_read_input_tokens
        if delta_usage.input_tokens is not None:
            self._usage.input_tokens = delta_usage.input_tokens
        if delta_usage.output_tokens is not None:  # type: ignore
            self._usage.output_tokens = delta_usage.output_tokens

    async def _convert_non_stream_response(
        self,
        response: AnthropicMessage,
    ) -> AsyncIterator[StreamedMessagePart]:
        self._id = response.id
        self._usage = response.usage
        for block in response.content:
            match block.type:
                case "text":
                    yield TextPart(text=block.text)
                case "thinking":
                    yield ThinkPart(think=block.thinking, encrypted=block.signature)
                case "redacted_thinking":
                    yield ThinkPart(think="", encrypted=block.data)
                case "tool_use":
                    yield ToolCall(
                        id=block.id,
                        function=ToolCall.FunctionBody(
                            name=block.name, arguments=json.dumps(block.input)
                        ),
                    )
                case _:
                    continue

    async def _convert_stream_response(
        self,
        manager: AsyncStream[RawMessageStreamEvent],
    ) -> AsyncIterator[StreamedMessagePart]:
        try:
            async with manager as stream:
                async for event in stream:
                    if isinstance(event, MessageStartEvent):
                        self._id = event.message.id
                        # Capture initial usage from start event
                        # (contains initial prompt/input token usage)
                        self._usage = event.message.usage
                    elif isinstance(event, RawContentBlockStartEvent):
                        block = event.content_block
                        match block.type:
                            case "text":
                                yield TextPart(text=block.text)
                            case "thinking":
                                yield ThinkPart(think=block.thinking)
                            case "redacted_thinking":
                                yield ThinkPart(think="", encrypted=block.data)
                            case "tool_use":
                                yield ToolCall(
                                    id=block.id,
                                    function=ToolCall.FunctionBody(name=block.name, arguments=""),
                                )
                            case "server_tool_use" | "web_search_tool_result":
                                # ignore
                                continue
                    elif isinstance(event, RawContentBlockDeltaEvent):
                        delta = event.delta
                        match delta.type:
                            case "text_delta":
                                yield TextPart(text=delta.text)
                            case "thinking_delta":
                                yield ThinkPart(think=delta.thinking)
                            case "input_json_delta":
                                yield ToolCallPart(arguments_part=delta.partial_json)
                            case "signature_delta":
                                yield ThinkPart(think="", encrypted=delta.signature)
                            case "citations_delta":
                                # ignore
                                continue
                    elif isinstance(event, MessageDeltaEvent):
                        if event.usage:
                            self._update_usage(event.usage)
                    elif isinstance(event, MessageStopEvent):
                        continue
        except (AnthropicError, httpx.HTTPError) as exc:
            raise _convert_error(exc) from exc


def _convert_tool(tool: Tool) -> ToolParam:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


def _is_tool_result_only(content: object) -> bool:
    """当且仅当 ``content`` 是仅包含 tool_result 块的非空列表时为 True。

    守护 ``generate()`` 中的并行工具结果合并：我们仅在双方都携带纯工具
    结果时才折叠连续的用户消息，当用户消息混合了文本、图像或其他任何
    内容时则不折叠。
    """
    if not isinstance(content, list) or not content:
        return False
    blocks = cast(list[ContentBlockParam], content)
    return all(b["type"] == "tool_result" for b in blocks)


def _tool_result_message_to_block(
    tool_call_id: str, content: str | list[ContentPart]
) -> ToolResultBlockParam:
    block_content: str | list[ToolResultContent]
    # 如果 tool_result_process 是 `extract_text`，我们将所有文本部分合并为一个字符串
    if isinstance(content, str):
        block_content = content
    else:
        # 否则，将部分映射到内容块
        blocks: list[ToolResultContent] = []
        for part in content:
            if isinstance(part, TextPart):
                if part.text:
                    blocks.append(TextBlockParam(type="text", text=part.text))
            elif isinstance(part, ImageURLPart):
                blocks.append(_image_url_part_to_anthropic(part))
            else:
                # https://docs.claude.com/en/docs/build-with-claude/files#file-types-and-content-blocks
                # Anthropic API 支持非常有限的文件类型
                raise ChatProviderError(
                    f"Anthropic API does not support {type(part)} in tool result"
                )
        block_content = blocks

    return ToolResultBlockParam(
        type="tool_result",
        tool_use_id=tool_call_id,
        content=block_content,
    )


def _image_url_part_to_anthropic(part: ImageURLPart) -> ImageBlockParam:
    url = part.image_url.url
    # data:[<media-type>][;base64],<data>
    if url.startswith("data:"):
        res = url[5:].split(";base64,", 1)
        if len(res) != 2:
            raise ChatProviderError(f"Invalid data URL for image: {url}")
        media_type, data = res
        if media_type not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            raise ChatProviderError(
                f"Unsupported media type for base64 image: {media_type}, url: {url}"
            )
        return ImageBlockParam(
            type="image",
            source=Base64ImageSourceParam(
                type="base64",
                data=data,
                media_type=media_type,
            ),
        )
    else:
        return ImageBlockParam(
            type="image",
            source=URLImageSourceParam(type="url", url=url),
        )


def _convert_error(error: AnthropicError | httpx.HTTPError) -> ChatProviderError:
    # httpx errors may leak through the Anthropic SDK during streaming;
    # delegate to the shared converter.
    if isinstance(error, httpx.HTTPError):
        return convert_httpx_error(error)
    # Anthropic SDK errors — check subclasses before parents to avoid
    # misclassification (e.g. APITimeoutError inherits APIConnectionError).
    if isinstance(error, AnthropicAPIStatusError):
        return APIStatusError(error.status_code, str(error))
    if isinstance(error, AnthropicAuthenticationError):
        return APIStatusError(getattr(error, "status_code", 401), str(error))
    if isinstance(error, AnthropicPermissionDeniedError):
        return APIStatusError(getattr(error, "status_code", 403), str(error))
    if isinstance(error, AnthropicRateLimitError):
        return APIStatusError(getattr(error, "status_code", 429), str(error))
    if isinstance(error, AnthropicAPITimeoutError):
        return APITimeoutError(str(error))
    if isinstance(error, AnthropicAPIConnectionError):
        return APIConnectionError(str(error))
    return ChatProviderError(f"Anthropic error: {error}")
