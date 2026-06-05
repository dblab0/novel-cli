"""消息压缩模块。

提供对话历史的压缩功能，用于在上下文窗口接近限制时自动压缩历史消息，
保留关键信息的同时减少 token 使用量。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

import kosong
from kosong.chat_provider import TokenUsage
from kosong.message import Message
from kosong.tooling.empty import EmptyToolset

import novel_cli.prompts as prompts
from novel_cli.llm import LLM
from novel_cli.soul.message import system
from novel_cli.utils.logging import logger
from novel_cli.wire.types import ContentPart, TextPart, ThinkPart


class CompactionResult(NamedTuple):
    """压缩结果，包含压缩后的消息和 token 使用量。

    Attributes:
        messages: 压缩后的消息序列。
        usage: LLM 调用的 token 使用量，可能为 None。
    """

    messages: Sequence[Message]
    usage: TokenUsage | None

    @property
    def estimated_token_count(self) -> int:
        """估算压缩后消息的 token 数量。

        当 LLM 使用量可用时，``usage.output`` 提供生成摘要（第一条消息）的精确 token 数量。
        保留的消息（所有后续消息）通过文本长度估算。

        当使用量不可用时（未进行压缩 LLM 调用），所有消息都通过文本长度估算。

        该估算有意保持保守——它将在下一次 LLM 调用时被真实值替换。

        Returns:
            估算的 token 数量。
        """
        if self.usage is not None and len(self.messages) > 0:
            summary_tokens = self.usage.output
            preserved_tokens = estimate_text_tokens(self.messages[1:])
            return summary_tokens + preserved_tokens

        return estimate_text_tokens(self.messages)


def estimate_text_tokens(messages: Sequence[Message]) -> int:
    """使用基于字符的启发式方法估算消息文本内容的 token 数量。

    Args:
        messages: 待估算的消息序列。

    Returns:
        估算的 token 数量。
    """
    total_chars = 0
    for msg in messages:
        for part in msg.content:
            if isinstance(part, TextPart):
                total_chars += len(part.text)
    # 英文约 4 个字符对应 1 个 token；对 CJK 文本略有低估，
    # 但这是一个临时估算，会在下一次 LLM 调用时被修正。
    return total_chars // 4


def should_auto_compact(
    token_count: int,
    max_context_size: int,
    *,
    trigger_ratio: float,
    reserved_context_size: int,
) -> bool:
    """判断是否应触发自动压缩。

    当满足以下任一条件时返回 True（以先触发者为准）：
    - 基于比例：token_count >= max_context_size * trigger_ratio
    - 基于预留：token_count + reserved_context_size >= max_context_size

    Args:
        token_count: 当前 token 数量。
        max_context_size: 最大上下文大小。
        trigger_ratio: 触发压缩的比例阈值。
        reserved_context_size: 预留的上下文大小。

    Returns:
        如果应触发自动压缩则返回 True，否则返回 False。
    """
    return (
        token_count >= max_context_size * trigger_ratio
        or token_count + reserved_context_size >= max_context_size
    )


@runtime_checkable
class Compaction(Protocol):
    """压缩协议，定义消息压缩的接口。"""

    async def compact(
        self, messages: Sequence[Message], llm: LLM, *, custom_instruction: str = ""
    ) -> CompactionResult:
        """将消息序列压缩为新的消息序列。

        Args:
            messages: 待压缩的消息序列。
            llm: 用于压缩的 LLM 实例。
            custom_instruction: 可选的用户指令，用于引导压缩重点。

        Returns:
            压缩结果，包含压缩后的消息和压缩 LLM 调用的 token 使用量。

        Raises:
            ChatProviderError: 当 chat provider 返回错误时抛出。
        """
        ...


if TYPE_CHECKING:

    def type_check(simple: SimpleCompaction):
        _: Compaction = simple


class SimpleCompaction:
    """简单压缩实现，通过 LLM 生成摘要来压缩消息历史。

    Args:
        max_preserved_messages: 保留的最新消息数量，默认为 2。

    Attributes:
        max_preserved_messages: 保留的最新消息数量。
    """

    def __init__(self, max_preserved_messages: int = 2) -> None:
        self.max_preserved_messages = max_preserved_messages

    async def compact(
        self, messages: Sequence[Message], llm: LLM, *, custom_instruction: str = ""
    ) -> CompactionResult:
        """执行消息压缩。

        Args:
            messages: 待压缩的消息序列。
            llm: 用于压缩的 LLM 实例。
            custom_instruction: 可选的用户指令，用于引导压缩重点。

        Returns:
            压缩结果，包含压缩后的消息和 token 使用量。
        """
        compact_message, to_preserve = self.prepare(messages, custom_instruction=custom_instruction)
        if compact_message is None:
            return CompactionResult(messages=to_preserve, usage=None)

        # 调用 kosong.step 获取压缩后的上下文
        # TODO: 设置最大完成 token 数
        logger.debug("Compacting context...")
        result = await kosong.step(
            chat_provider=llm.chat_provider,
            system_prompt="You are a helpful assistant that compacts conversation context.",
            toolset=EmptyToolset(),
            history=[compact_message],
        )
        if result.usage:
            logger.debug(
                "Compaction used {input} input tokens and {output} output tokens",
                input=result.usage.input,
                output=result.usage.output,
            )

        content: list[ContentPart] = [
            system("Previous context has been compacted. Here is the compaction output:")
        ]
        compacted_msg = result.message

        # 移除思考部分（如果有的话）
        content.extend(part for part in compacted_msg.content if not isinstance(part, ThinkPart))
        compacted_messages: list[Message] = [Message(role="user", content=content)]
        compacted_messages.extend(to_preserve)
        return CompactionResult(messages=compacted_messages, usage=result.usage)

    class PrepareResult(NamedTuple):
        """准备结果，包含待压缩消息和待保留消息。

        Attributes:
            compact_message: 待压缩的消息，可能为 None。
            to_preserve: 待保留的消息序列。
        """

        compact_message: Message | None
        to_preserve: Sequence[Message]

    def prepare(
        self, messages: Sequence[Message], *, custom_instruction: str = ""
    ) -> PrepareResult:
        """准备压缩所需的消息。

        将消息分为待压缩部分和待保留部分，并构建压缩请求消息。

        Args:
            messages: 原始消息序列。
            custom_instruction: 可选的用户指令，用于引导压缩重点。

        Returns:
            准备结果，包含待压缩消息和待保留消息。
        """
        if not messages or self.max_preserved_messages <= 0:
            return self.PrepareResult(compact_message=None, to_preserve=messages)

        history = list(messages)
        preserve_start_index = len(history)
        n_preserved = 0
        for index in range(len(history) - 1, -1, -1):
            if history[index].role in {"user", "assistant"}:
                n_preserved += 1
                if n_preserved == self.max_preserved_messages:
                    preserve_start_index = index
                    break

        if n_preserved < self.max_preserved_messages:
            return self.PrepareResult(compact_message=None, to_preserve=messages)

        to_compact = history[:preserve_start_index]
        to_preserve = history[preserve_start_index:]

        if not to_compact:
            # 希望这不会超过上下文大小限制
            return self.PrepareResult(compact_message=None, to_preserve=to_preserve)

        # 创建压缩输入消息
        compact_message = Message(role="user", content=[])
        for i, msg in enumerate(to_compact):
            compact_message.content.append(
                TextPart(text=f"## Message {i + 1}\nRole: {msg.role}\nContent:\n")
            )
            compact_message.content.extend(
                part for part in msg.content if isinstance(part, TextPart)
            )
        prompt_text = "\n" + prompts.COMPACT
        if custom_instruction:
            prompt_text += (
                "\n\n**User's Custom Compaction Instruction:**\n"
                "The user has specifically requested the following focus during compaction. "
                "You MUST prioritize this instruction above the default compression priorities:\n"
                f"{custom_instruction}"
            )
        compact_message.content.append(TextPart(text=prompt_text))
        return self.PrepareResult(compact_message=compact_message, to_preserve=to_preserve)