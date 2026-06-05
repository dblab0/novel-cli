"""上下文管理模块。

本模块提供 Context 类，用于管理 soul 的对话历史和上下文状态。
支持上下文的持久化存储、检查点（checkpoint）创建与回滚、以及消息追加等操作。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import aiofiles
import aiofiles.os
from kosong.message import Message
from pydantic import ValidationError

from novel_cli.soul.compaction import estimate_text_tokens
from novel_cli.soul.message import system
from novel_cli.utils.logging import logger
from novel_cli.utils.path import next_available_rotation


class Context:
    """上下文管理器，负责管理 soul 的对话历史和状态。

    使用文件后端持久化存储对话历史，支持检查点创建、回滚和清理操作。

    Attributes:
        _file_backend: 用于持久化存储的文件路径。
        _history: 对话消息历史记录。
        _token_count: 已确认的 token 数量。
        _pending_token_estimate: 待确认的 token 估算值。
        _next_checkpoint_id: 下一个检查点的 ID。
        _system_prompt: 系统提示词。
    """

    def __init__(self, file_backend: Path):
        """初始化上下文管理器。

        Args:
            file_backend: 用于持久化存储的文件路径。
        """
        self._file_backend = file_backend
        self._history: list[Message] = []
        self._token_count: int = 0
        self._pending_token_estimate: int = 0
        self._next_checkpoint_id: int = 0
        # 下一个检查点的 ID，从 0 开始，每次创建检查点后递增
        self._system_prompt: str | None = None

    async def restore(self) -> bool:
        """从文件后端恢复上下文。

        从持久化存储文件中读取并恢复对话历史和状态。

        Returns:
            如果成功恢复上下文则返回 True，如果文件不存在或为空则返回 False。

        Raises:
            RuntimeError: 当上下文存储已被修改时抛出。
        """
        logger.debug("Restoring context from file: {file_backend}", file_backend=self._file_backend)
        if self._history:
            logger.error("The context storage is already modified")
            raise RuntimeError("The context storage is already modified")
        if not self._file_backend.exists():
            logger.debug("No context file found, skipping restoration")
            return False
        if self._file_backend.stat().st_size == 0:
            logger.debug("Empty context file, skipping restoration")
            return False

        messages_after_last_usage: list[Message] = []
        async with aiofiles.open(self._file_backend, encoding="utf-8", errors="replace") as f:
            line_no = 0
            async for line in f:
                line_no += 1
                if not line.strip():
                    continue
                line_json = self._parse_context_line(
                    line,
                    file_backend=self._file_backend,
                    line_no=line_no,
                )
                if line_json is None:
                    continue
                self._apply_context_record(
                    line_json,
                    history=self._history,
                    messages_after_last_usage=messages_after_last_usage,
                    file_backend=self._file_backend,
                    line_no=line_no,
                )

        self._pending_token_estimate = estimate_text_tokens(messages_after_last_usage)
        return True

    @property
    def history(self) -> Sequence[Message]:
        """获取对话历史记录。"""
        return self._history

    @property
    def token_count(self) -> int:
        """获取已确认的 token 数量。"""
        return self._token_count

    @property
    def token_count_with_pending(self) -> int:
        """获取包含待确认估算的 token 总数。"""
        return self._token_count + self._pending_token_estimate

    @property
    def n_checkpoints(self) -> int:
        """获取已创建的检查点数量。"""
        return self._next_checkpoint_id

    @property
    def system_prompt(self) -> str | None:
        """获取系统提示词。"""
        return self._system_prompt

    @property
    def file_backend(self) -> Path:
        """获取文件后端路径。"""
        return self._file_backend

    async def write_system_prompt(self, prompt: str) -> None:
        """将系统提示词写入上下文文件的首条记录。

        如果文件为空，则直接写入。如果文件已有内容（例如没有系统提示词的旧会话），
        则通过临时文件原子性地将提示词追加到文件开头，以避免崩溃时数据损坏，
        同时避免将整个文件加载到内存中。

        Args:
            prompt: 要写入的系统提示词内容。
        """
        prompt_line = json.dumps({"role": "_system_prompt", "content": prompt}) + "\n"

        def _write_system_prompt_sync() -> None:
            if not self._file_backend.exists() or self._file_backend.stat().st_size == 0:
                self._file_backend.write_text(prompt_line, encoding="utf-8")
                return

            tmp_path = self._file_backend.with_suffix(".tmp")
            with (
                tmp_path.open("w", encoding="utf-8") as tmp_f,
                self._file_backend.open(encoding="utf-8") as src_f,
            ):
                tmp_f.write(prompt_line)
                while True:
                    chunk = src_f.read(64 * 1024)
                    if not chunk:
                        break
                    tmp_f.write(chunk)
            tmp_path.replace(self._file_backend)

        await asyncio.to_thread(_write_system_prompt_sync)

        self._system_prompt = prompt

    async def checkpoint(self, add_user_message: bool):
        """创建上下文检查点。

        Args:
            add_user_message: 是否在检查点后添加一条用户消息。
        """
        checkpoint_id = self._next_checkpoint_id
        self._next_checkpoint_id += 1
        logger.debug("Checkpointing, ID: {id}", id=checkpoint_id)

        async with aiofiles.open(self._file_backend, "a", encoding="utf-8") as f:
            await f.write(json.dumps({"role": "_checkpoint", "id": checkpoint_id}) + "\n")
        if add_user_message:
            await self.append_message(
                Message(role="user", content=[system(f"CHECKPOINT {checkpoint_id}")])
            )

    async def revert_to(self, checkpoint_id: int):
        """将上下文回滚到指定的检查点。

        回滚后，指定检查点及其后的所有内容将从上下文中移除。
        文件后端将被轮转备份。

        Args:
            checkpoint_id: 要回滚到的检查点 ID，0 表示第一个检查点。

        Raises:
            ValueError: 当指定的检查点不存在时抛出。
            RuntimeError: 当无法找到可用的轮转路径时抛出。
        """

        logger.debug("Reverting checkpoint, ID: {id}", id=checkpoint_id)
        if checkpoint_id >= self._next_checkpoint_id:
            logger.error("Checkpoint {checkpoint_id} does not exist", checkpoint_id=checkpoint_id)
            raise ValueError(f"Checkpoint {checkpoint_id} does not exist")

        # 轮转上下文文件
        rotated_file_path = await next_available_rotation(self._file_backend)
        if rotated_file_path is None:
            logger.error("No available rotation path found")
            raise RuntimeError("No available rotation path found")
        await aiofiles.os.replace(self._file_backend, rotated_file_path)
        logger.debug(
            "Rotated context file: {rotated_file_path}", rotated_file_path=rotated_file_path
        )

        # 恢复上下文直到指定检查点
        self._history.clear()
        self._token_count = 0
        self._next_checkpoint_id = 0
        self._system_prompt = None
        messages_after_last_usage: list[Message] = []
        async with (
            aiofiles.open(rotated_file_path, encoding="utf-8", errors="replace") as old_file,
            aiofiles.open(self._file_backend, "w", encoding="utf-8") as new_file,
        ):
            line_no = 0
            async for line in old_file:
                line_no += 1
                if not line.strip():
                    continue

                line_json = self._parse_context_line(
                    line,
                    file_backend=rotated_file_path,
                    line_no=line_no,
                )
                if line_json is None:
                    continue
                if line_json.get("role") == "_checkpoint" and line_json.get("id") == checkpoint_id:
                    break

                keep_line = self._apply_context_record(
                    line_json,
                    history=self._history,
                    messages_after_last_usage=messages_after_last_usage,
                    file_backend=rotated_file_path,
                    line_no=line_no,
                )
                if keep_line:
                    await new_file.write(line)

        self._pending_token_estimate = estimate_text_tokens(messages_after_last_usage)

    async def clear(self):
        """清空上下文历史。

        此操作几乎等同于 revert_to(0)，但不依赖第一个检查点存在的假设。
        文件后端将被轮转备份。

        Raises:
            RuntimeError: 当无法找到可用的轮转路径时抛出。
        """

        logger.debug("Clearing context")

        # 轮转上下文文件
        rotated_file_path = await next_available_rotation(self._file_backend)
        if rotated_file_path is None:
            logger.error("No available rotation path found")
            raise RuntimeError("No available rotation path found")
        await aiofiles.os.replace(self._file_backend, rotated_file_path)
        self._file_backend.touch()
        logger.debug(
            "Rotated context file: {rotated_file_path}", rotated_file_path=rotated_file_path
        )

        self._history.clear()
        self._token_count = 0
        self._pending_token_estimate = 0
        self._next_checkpoint_id = 0
        self._system_prompt = None

    async def append_message(self, message: Message | Sequence[Message]):
        """向上下文追加消息。

        Args:
            message: 要追加的消息，可以是单条消息或消息序列。
        """
        logger.debug("Appending message(s) to context: {message}", message=message)
        messages = [message] if isinstance(message, Message) else message
        self._history.extend(messages)
        self._pending_token_estimate += estimate_text_tokens(messages)

        async with aiofiles.open(self._file_backend, "a", encoding="utf-8") as f:
            for message in messages:
                await f.write(message.model_dump_json(exclude_none=True) + "\n")

    async def update_token_count(self, token_count: int):
        """更新上下文中的 token 计数。

        Args:
            token_count: 新的 token 计数值。
        """
        logger.debug("Updating token count in context: {token_count}", token_count=token_count)
        self._token_count = token_count
        self._pending_token_estimate = 0

        async with aiofiles.open(self._file_backend, "a", encoding="utf-8") as f:
            await f.write(json.dumps({"role": "_usage", "token_count": token_count}) + "\n")

    def _parse_context_line(
        self,
        line: str,
        *,
        file_backend: Path,
        line_no: int,
    ) -> dict[str, Any] | None:
        """解析上下文文件中的一行 JSON 记录。

        Args:
            line: 要解析的行内容。
            file_backend: 上下文文件路径（用于日志记录）。
            line_no: 行号（用于日志记录）。

        Returns:
            解析后的字典对象，如果解析失败或不是有效对象则返回 None。
        """
        try:
            line_json = json.loads(line, strict=False)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Skipping malformed context line {line_no} in {file}: {error}",
                line_no=line_no,
                file=file_backend,
                error=exc,
            )
            return None
        if not isinstance(line_json, dict):
            logger.warning(
                "Skipping non-object context line {line_no} in {file}",
                line_no=line_no,
                file=file_backend,
            )
            return None
        return cast(dict[str, Any], line_json)

    def _apply_context_record(
        self,
        line_json: dict[str, Any],
        *,
        history: list[Message],
        messages_after_last_usage: list[Message],
        file_backend: Path,
        line_no: int,
    ) -> bool:
        """应用上下文记录到内部状态。

        Args:
            line_json: 解析后的 JSON 记录。
            history: 对话历史列表，用于追加消息。
            messages_after_last_usage: 上次使用记录后的消息列表。
            file_backend: 上下文文件路径（用于日志记录）。
            line_no: 行号（用于日志记录）。

        Returns:
            如果记录被成功处理且应保留在文件中则返回 True，否则返回 False。
        """
        role = line_json.get("role")
        if not isinstance(role, str):
            logger.warning(
                "Skipping context line {line_no} in {file}: missing or invalid role",
                line_no=line_no,
                file=file_backend,
            )
            return False
        if role == "_system_prompt":
            content = line_json.get("content")
            if not isinstance(content, str):
                logger.warning(
                    "Skipping invalid system prompt line {line_no} in {file}",
                    line_no=line_no,
                    file=file_backend,
                )
                return False
            self._system_prompt = content
            return True
        if role == "_usage":
            token_count = line_json.get("token_count")
            if not isinstance(token_count, int):
                logger.warning(
                    "Skipping invalid usage line {line_no} in {file}",
                    line_no=line_no,
                    file=file_backend,
                )
                return False
            self._token_count = token_count
            messages_after_last_usage.clear()
            return True
        if role == "_checkpoint":
            checkpoint_id = line_json.get("id")
            if not isinstance(checkpoint_id, int):
                logger.warning(
                    "Skipping invalid checkpoint line {line_no} in {file}",
                    line_no=line_no,
                    file=file_backend,
                )
                return False
            self._next_checkpoint_id = checkpoint_id + 1
            return True
        try:
            message = Message.model_validate(line_json)
        except ValidationError as exc:
            logger.warning(
                "Skipping invalid context message line {line_no} in {file}: {error}",
                line_no=line_no,
                file=file_backend,
                error=exc,
            )
            return False
        history.append(message)
        messages_after_last_usage.append(message)
        return True
