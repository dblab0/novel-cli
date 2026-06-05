"""会话历史回放功能模块。

提供回放最近用户对话轮次的功能，用于在启动时恢复会话的可视化状态。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from kosong.message import ContentPart, Message
from kosong.tooling import ToolError, ToolOk

from novel_cli.notifications.llm import is_notification_message
from novel_cli.soul.message import is_system_reminder_message
from novel_cli.ui.shell.console import console
from novel_cli.ui.shell.echo import render_user_echo
from novel_cli.ui.shell.visualize import visualize
from novel_cli.utils.aioqueue import QueueShutDown
from novel_cli.utils.logging import logger
from novel_cli.utils.message import message_stringify
from novel_cli.utils.slashcmd import parse_slash_command_call
from novel_cli.wire import Wire
from novel_cli.wire.file import WireFile
from novel_cli.wire.types import (
    Event,
    StatusUpdate,
    SteerInput,
    StepBegin,
    TextPart,
    ToolResult,
    TurnBegin,
    is_event,
)

MAX_REPLAY_TURNS = 5


@dataclass(slots=True)
class _ReplayTurn:
    """回放对话轮次数据。

    Attributes:
        user_message: 用户消息。
        events: 该轮次的事件列表。
        n_steps: 步骤计数。
    """
    user_message: Message
    events: list[Event]
    n_steps: int = 0


async def replay_recent_history(
    history: Sequence[Message],
    *,
    wire_file: WireFile | None = None,
) -> None:
    """回放最近用户发起的对话轮次。

    从提供的历史消息或 wire 文件中回放最近用户发起的对话轮次。

    Args:
        history: 消息历史序列。
        wire_file: wire 文件实例（可选）。
    """
    if not history:
        # 如果上下文历史为空，这可能是新会话或上下文已被清除
        return

    start_idx = _find_replay_start(history)
    history_turns = (
        [] if start_idx is None else _build_replay_turns_from_history(history[start_idx:])
    )
    turns = await _build_replay_turns_from_wire(wire_file)
    if not turns or (history_turns and not _same_user_turns(turns, history_turns)):
        turns = history_turns
    if not turns:
        return

    for turn in turns:
        wire = Wire()
        console.print(render_user_echo(turn.user_message))
        ui_task = asyncio.create_task(
            visualize(wire.ui_side(merge=False), initial_status=StatusUpdate())
        )
        for event in turn.events:
            wire.soul_side.send(event)
            await asyncio.sleep(0)  # yield to UI loop
        wire.shutdown()
        with contextlib.suppress(QueueShutDown):
            await ui_task


async def _build_replay_turns_from_wire(wire_file: WireFile | None) -> list[_ReplayTurn]:
    """从 wire 文件构建回放对话轮次。

    Args:
        wire_file: wire 文件实例。

    Returns:
        回放对话轮次列表，若文件无效或过大则返回空列表。
    """
    if wire_file is None or not wire_file.path.exists():
        return []

    size = wire_file.path.stat().st_size
    if size > 20 * 1024 * 1024:
        logger.info(
            "Wire file too large for replay, skipping: {file} ({size} bytes)",
            file=wire_file.path,
            size=size,
        )
        return []

    turns: deque[_ReplayTurn] = deque(maxlen=MAX_REPLAY_TURNS)
    try:
        async for record in wire_file.iter_records():
            wire_msg = record.to_wire_message()

            if isinstance(wire_msg, TurnBegin):
                if _is_clear_command_input(wire_msg.user_input):
                    turns.clear()
                    continue
                turns.append(
                    _ReplayTurn(
                        user_message=_message_from_user_input(wire_msg.user_input),
                        events=[],
                    )
                )
                continue

            if isinstance(wire_msg, SteerInput):
                turns.append(
                    _ReplayTurn(
                        user_message=_message_from_user_input(wire_msg.user_input),
                        events=[],
                    )
                )
                continue

            if not is_event(wire_msg) or not turns:
                continue

            current_turn = turns[-1]
            if isinstance(wire_msg, StepBegin):
                current_turn.n_steps = wire_msg.n
            current_turn.events.append(wire_msg)
    except Exception:
        logger.exception("Failed to build replay turns from wire file {file}:", file=wire_file.path)
        return []
    return list(turns)


def _message_from_user_input(user_input: str | list[ContentPart]) -> Message:
    """从用户输入构建消息对象。

    Args:
        user_input: 用户输入字符串或内容部分列表。

    Returns:
        构建的用户消息对象。
    """
    content = cast(
        list[ContentPart],
        list(user_input) if isinstance(user_input, list) else [TextPart(text=user_input)],
    )
    return Message(role="user", content=content)


def _same_user_turns(lhs: Sequence[_ReplayTurn], rhs: Sequence[_ReplayTurn]) -> bool:
    """比较两个对话轮次序列是否相同。

    Args:
        lhs: 左侧对话轮次序列。
        rhs: 右侧对话轮次序列。

    Returns:
        是否相同。
    """
    return [message_stringify(turn.user_message) for turn in lhs] == [
        message_stringify(turn.user_message) for turn in rhs
    ]


def _is_clear_command_input(user_input: str | list[ContentPart]) -> bool:
    """判断用户输入是否为清除命令。

    Args:
        user_input: 用户输入。

    Returns:
        是否为 clear 或 reset 命令。
    """
    if isinstance(user_input, list):
        text = Message(role="user", content=user_input).extract_text(" ").strip()
    else:
        text = str(user_input).strip()
    call = parse_slash_command_call(text)
    if call is None:
        return False
    return call.name in {"clear", "reset"}


def _is_user_message(message: Message) -> bool:
    """判断消息是否为用户发起的消息。

    Args:
        message: 待判断的消息。

    Returns:
        是否为用户消息。

    Note:
        FIXME: 应考虑非文本工具调用结果，它们以用户消息形式发送。
    """
    if message.role != "user":
        return False
    if message.extract_text().startswith("<system>CHECKPOINT"):
        return False
    if is_notification_message(message):
        return False
    return not is_system_reminder_message(message)


def _find_replay_start(history: Sequence[Message]) -> int | None:
    """查找历史中的回放起始索引。

    Args:
        history: 消息历史序列。

    Returns:
        回放起始索引，若无用户消息则返回 None。
    """
    indices = [idx for idx, message in enumerate(history) if _is_user_message(message)]
    if not indices:
        return None
    # 只回放最近的 MAX_REPLAY_TURNS 条消息
    return indices[max(0, len(indices) - MAX_REPLAY_TURNS)]


def _build_replay_turns_from_history(history: Sequence[Message]) -> list[_ReplayTurn]:
    """从消息历史构建回放对话轮次。

    Args:
        history: 消息历史序列。

    Returns:
        回放对话轮次列表。
    """
    turns: list[_ReplayTurn] = []
    current_turn: _ReplayTurn | None = None
    for message in history:
        if _is_user_message(message):
            # 开始新轮次
            if current_turn is not None:
                turns.append(current_turn)
            current_turn = _ReplayTurn(user_message=message, events=[])
        elif message.role == "assistant":
            if current_turn is None:
                continue
            current_turn.n_steps += 1
            current_turn.events.append(StepBegin(n=current_turn.n_steps))
            current_turn.events.extend(message.content)
            current_turn.events.extend(message.tool_calls or [])
        elif message.role == "tool":
            if current_turn is None:
                continue
            assert message.tool_call_id is not None
            if any(
                isinstance(part, TextPart) and part.text.startswith("<system>ERROR")
                for part in message.content
            ):
                result = ToolError(message="", output="", brief="")
            else:
                result = ToolOk(output=message.content)
            current_turn.events.append(
                ToolResult(tool_call_id=message.tool_call_id, return_value=result)
            )
    if current_turn is not None:
        turns.append(current_turn)
    return turns
