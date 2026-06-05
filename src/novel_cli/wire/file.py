"""Wire 文件持久化模块。

本模块提供 Wire 消息的文件持久化功能，支持将 Wire 消息写入 JSONL 格式文件，
并支持从文件中读取和解析消息记录。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiofiles
from pydantic import BaseModel, ConfigDict, ValidationError

from novel_cli.utils.logging import logger
from novel_cli.wire.protocol import WIRE_PROTOCOL_LEGACY_VERSION, WIRE_PROTOCOL_VERSION
from novel_cli.wire.types import WireMessage, WireMessageEnvelope


class WireFileMetadata(BaseModel):
    """Wire 文件元数据，存储在 wire.jsonl 文件的首行。

    Attributes:
        type: 类型标识，固定为 "metadata"。
        protocol_version: Wire 协议版本号。
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["metadata"] = "metadata"
    protocol_version: str


class WireMessageRecord(BaseModel):
    """Wire 消息的持久化记录。

    Attributes:
        timestamp: 消息记录的时间戳。
        message: Wire 消息的封装对象。
    """

    model_config = ConfigDict(extra="ignore")

    timestamp: float
    message: WireMessageEnvelope

    @classmethod
    def from_wire_message(cls, msg: WireMessage, *, timestamp: float) -> WireMessageRecord:
        """从 Wire 消息创建持久化记录。

        Args:
            msg: Wire 消息对象。
            timestamp: 消息的时间戳。

        Returns:
            创建的 Wire 消息记录。
        """
        return cls(timestamp=timestamp, message=WireMessageEnvelope.from_wire_message(msg))

    def to_wire_message(self) -> WireMessage:
        """将记录转换为 Wire 消息对象。

        Returns:
            转换后的 Wire 消息。
        """
        return self.message.to_wire_message()


def parse_wire_file_metadata(line: str) -> WireFileMetadata | None:
    """解析 Wire 文件的元数据行。

    Args:
        line: 文件中的一行字符串。

    Returns:
        如果是元数据行则返回解析后的 WireFileMetadata，否则返回 None。
    """
    try:
        return WireFileMetadata.model_validate_json(line)
    except (ValidationError, ValueError):
        return None


def parse_wire_file_line(line: str) -> WireFileMetadata | WireMessageRecord:
    """解析 Wire 文件的一行内容。

    Args:
        line: 文件中的一行字符串。

    Returns:
        解析后的元数据或消息记录对象。
    """
    metadata = parse_wire_file_metadata(line)
    if metadata is not None:
        return metadata
    return WireMessageRecord.model_validate_json(line)


@dataclass(slots=True)
class WireFile:
    """Wire 文件类，管理 Wire 消息的文件持久化。

    Attributes:
        path: Wire 文件的路径。
        protocol_version: Wire 协议版本号。
    """

    path: Path
    protocol_version: str = WIRE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        """初始化后处理，检测现有文件的协议版本。"""
        if self.path.exists():
            version = _load_protocol_version(self.path)
            self.protocol_version = version if version is not None else WIRE_PROTOCOL_LEGACY_VERSION
        else:
            self.protocol_version = WIRE_PROTOCOL_VERSION

    def __str__(self) -> str:
        """返回文件路径的字符串表示。"""
        return str(self.path)

    @property
    def version(self) -> str:
        """获取 Wire 协议版本号。"""
        return self.protocol_version

    def is_empty(self) -> bool:
        """检查 Wire 文件是否为空。

        Returns:
            如果文件不存在或只有元数据行则返回 True，否则返回 False。
        """
        if not self.path.exists():
            return True
        try:
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if parse_wire_file_metadata(line) is not None:
                        continue
                    return False
        except OSError:
            logger.exception("Failed to read wire file {file}:", file=self.path)
            return False
        return True

    async def iter_records(self) -> AsyncIterator[WireMessageRecord]:
        """异步迭代文件中的所有消息记录。

        Yields:
            文件中的每个消息记录。
        """
        if not self.path.exists():
            return
        try:
            async with aiofiles.open(self.path, encoding="utf-8") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = parse_wire_file_line(line)
                    except Exception:
                        logger.exception(
                            "Failed to parse line in wire file {file}:", file=self.path
                        )
                        continue
                    if isinstance(parsed, WireFileMetadata):
                        continue
                    yield parsed
        except Exception:
            logger.exception("Failed to read wire file {file}:", file=self.path)

    async def append_message(self, msg: WireMessage, *, timestamp: float | None = None) -> None:
        """追加 Wire 消息到文件。

        Args:
            msg: 要追加的 Wire 消息。
            timestamp: 消息的时间戳，如果未提供则使用当前时间。
        """
        record = WireMessageRecord.from_wire_message(
            msg,
            timestamp=time.time() if timestamp is None else timestamp,
        )
        await self.append_record(record)

    async def append_record(self, record: WireMessageRecord) -> None:
        """追加消息记录到文件。

        Args:
            record: 要追加的消息记录。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not self.path.exists() or self.path.stat().st_size == 0
        async with aiofiles.open(self.path, mode="a", encoding="utf-8") as f:
            if needs_header:
                metadata = WireFileMetadata(protocol_version=self.protocol_version)
                await f.write(_dump_line(metadata))
            await f.write(_dump_line(record))


def _dump_line(model: BaseModel) -> str:
    """将模型对象序列化为 JSON 行。

    Args:
        model: 要序列化的 Pydantic 模型对象。

    Returns:
        JSON 格式的字符串，末尾带换行符。
    """
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False) + "\n"


def _load_protocol_version(path: Path) -> str | None:
    """从 Wire 文件中加载协议版本。

    Args:
        path: Wire 文件路径。

    Returns:
        协议版本号，如果文件不存在或无元数据则返回 None。
    """
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                metadata = parse_wire_file_metadata(line)
                if metadata is None:
                    return None
                return metadata.protocol_version
    except OSError:
        logger.exception("Failed to read wire file {file}:", file=path)
    return None
