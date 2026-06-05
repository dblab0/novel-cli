"""占位符管理模块。

本模块提供提示词中占位符的管理功能，包括粘贴文本占位符和图片占位符的处理。
占位符用于在用户界面中显示压缩形式的内容，同时保留原始数据供后续解析使用。

主要组件：
- AttachmentCache: 附件持久化缓存
- PlaceholderHandler: 占位符处理器协议
- PastedTextPlaceholderHandler: 粘贴文本占位符处理器
- ImagePlaceholderHandler: 图片占位符处理器
- PromptPlaceholderManager: 提示词占位符管理器
"""

from __future__ import annotations

import base64
import mimetypes
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image

from novel_cli.share import get_share_dir
from novel_cli.utils.envvar import get_env_int
from novel_cli.utils.logging import logger
from novel_cli.utils.media_tags import wrap_media_part
from novel_cli.utils.string import random_string
from novel_cli.wire.types import ContentPart, ImageURLPart, TextPart

# 默认提示词缓存根目录
_DEFAULT_PROMPT_CACHE_ROOT = get_share_dir() / "prompt-cache"
# 旧版提示词缓存根目录
_LEGACY_PROMPT_CACHE_ROOT = Path("/tmp/novel")

# 图片占位符正则表达式
_IMAGE_PLACEHOLDER_RE = re.compile(
    r"\[(?P<type>[a-zA-Z0-9_\-]+):(?P<id>[a-zA-Z0-9_\-\.]+)"
    r"(?:,(?P<width>\d+)x(?P<height>\d+))?\]"
)
# 粘贴文本占位符正则表达式
_PASTED_TEXT_PLACEHOLDER_RE = re.compile(
    r"\[Pasted text #(?P<id>\d+)(?: \+(?P<lines>\d+) lines?)?\]"
)

# 粘贴文本字符数阈值（超过此数值将创建占位符）
_TEXT_PASTE_CHAR_THRESHOLD = get_env_int("NOVEL_CLI_PASTE_CHAR_THRESHOLD", 1000)
# 粘贴文本行数阈值（超过此行数将创建占位符）
_TEXT_PASTE_LINE_THRESHOLD = get_env_int("NOVEL_CLI_PASTE_LINE_THRESHOLD", 15)


def sanitize_surrogates(text: str) -> str:
    """替换无法编码为 UTF-8 的孤立 UTF-16 代理字符。

    Windows 剪贴板数据有时包含来自内部使用 UTF-16 的应用程序的未配对代理字符。
    将此类字符串传递给 ``json.dumps`` 或写入 UTF-8 文件会引发 ``UnicodeEncodeError``，
    因此我们提前将其替换为 U+FFFD 替换字符。

    Args:
        text: 需要清理的原始文本。

    Returns:
        清理后的安全文本，不含孤立代理字符。
    """
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


def normalize_pasted_text(text: str) -> str:
    """将粘贴文本规范化为 prompt_toolkit 使用的换行格式。

    Args:
        text: 需要规范化的原始文本。

    Returns:
        规范化后的文本，统一使用 ``\\n`` 作为换行符。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def count_text_lines(text: str) -> int:
    """计算文本的行数。

    Args:
        text: 需要计算行数的文本。

    Returns:
        文本的行数（空文本返回 1）。
    """
    if not text:
        return 1
    return text.count("\n") + 1


def should_placeholderize_pasted_text(text: str) -> bool:
    """判断粘贴文本是否应该转换为占位符。

    当文本长度超过字符阈值或行数超过行数阈值时，应创建占位符。

    Args:
        text: 需要判断的文本。

    Returns:
        如果应该创建占位符返回 True，否则返回 False。
    """
    normalized = normalize_pasted_text(text)
    return (
        len(normalized) >= _TEXT_PASTE_CHAR_THRESHOLD
        or count_text_lines(normalized) >= _TEXT_PASTE_LINE_THRESHOLD
    )


def build_pasted_text_placeholder(paste_id: int, text: str) -> str:
    """构建粘贴文本的占位符字符串。

    Args:
        paste_id: 粘贴文本的唯一标识符。
        text: 粘贴的文本内容。

    Returns:
        占位符字符串，格式如 ``[Pasted text #1 +10 lines]``。
    """
    line_count = count_text_lines(text)
    if line_count <= 1:
        return f"[Pasted text #{paste_id}]"
    return f"[Pasted text #{paste_id} +{line_count} lines]"


def _guess_image_mime(path: Path) -> str:
    """猜测图片文件的 MIME 类型。

    Args:
        path: 图片文件路径。

    Returns:
        MIME 类型字符串，无法猜测时默认返回 ``image/png``。
    """
    mime, _ = mimetypes.guess_type(path.name)
    if mime:
        return mime
    return "image/png"


def _build_image_part(image_bytes: bytes, mime_type: str) -> ImageURLPart:
    """构建图片内容部分。

    Args:
        image_bytes: 图片的二进制数据。
        mime_type: 图片的 MIME 类型。

    Returns:
        包含 Base64 编码图片 URL 的 ImageURLPart 对象。
    """
    image_base64 = base64.b64encode(image_bytes).decode("ascii")
    return ImageURLPart(
        image_url=ImageURLPart.ImageURL(
            url=f"data:{mime_type};base64,{image_base64}",
        )
    )


# 缓存附件类型别名
type CachedAttachmentKind = Literal["image"]


@dataclass(slots=True)
class CachedAttachment:
    """缓存附件数据类。

    Attributes:
        kind: 附件类型（如 ``image``）。
        attachment_id: 附件的唯一标识符。
        path: 附件在缓存中的文件路径。
    """

    kind: CachedAttachmentKind
    attachment_id: str
    path: Path


class AttachmentCache:
    """附件持久化缓存，用于存储占位符对应的实际数据。

    缓存的数据可以在历史记录回溯时安全恢复，避免数据丢失。

    Attributes:
        _root: 缓存根目录。
        _legacy_roots: 旧版缓存根目录列表（用于兼容性）。
        _dir_map: 类型到子目录名称的映射。
        _payload_map: 缓存键到附件对象的映射。
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        legacy_roots: Sequence[Path] | None = None,
    ) -> None:
        """初始化附件缓存。

        Args:
            root: 缓存根目录，默认使用共享目录下的 prompt-cache。
            legacy_roots: 旧版缓存根目录列表，用于读取旧数据。
        """
        self._root = root or _DEFAULT_PROMPT_CACHE_ROOT
        self._legacy_roots = tuple(legacy_roots or (_LEGACY_PROMPT_CACHE_ROOT,))
        self._dir_map: dict[CachedAttachmentKind, str] = {"image": "images"}
        self._payload_map: dict[tuple[CachedAttachmentKind, str, str], CachedAttachment] = {}

    def _dir_for(self, kind: CachedAttachmentKind, *, root: Path | None = None) -> Path:
        """获取指定类型附件的存储目录。

        Args:
            kind: 附件类型。
            root: 可选的根目录，默认使用实例根目录。

        Returns:
            附件存储目录路径。
        """
        return (self._root if root is None else root) / self._dir_map[kind]

    def _ensure_dir(self, kind: CachedAttachmentKind) -> Path | None:
        """确保指定类型附件的存储目录存在。

        Args:
            kind: 附件类型。

        Returns:
            目录路径，创建失败返回 None。
        """
        path = self._dir_for(kind)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Failed to create attachment cache dir: {dir} ({error})",
                dir=path,
                error=exc,
            )
            return None
        return path

    def _reserve_id(self, dir_path: Path, suffix: str) -> str:
        """在指定目录中预留一个唯一的文件标识符。

        Args:
            dir_path: 目标目录路径。
            suffix: 文件名后缀（如 ``.png``）。

        Returns:
            生成的唯一文件名。
        """
        for _ in range(5):
            candidate = f"{random_string(8)}{suffix}"
            if not (dir_path / candidate).exists():
                return candidate
        return f"{random_string(12)}{suffix}"

    def store_bytes(
        self, kind: CachedAttachmentKind, suffix: str, payload: bytes
    ) -> CachedAttachment | None:
        """将二进制数据存储到缓存中。

        如果相同内容已存在缓存，则直接返回已有的缓存对象。

        Args:
            kind: 附件类型。
            suffix: 文件名后缀。
            payload: 二进制数据内容。

        Returns:
            缓存附件对象，存储失败返回 None。
        """
        dir_path = self._ensure_dir(kind)
        if dir_path is None:
            return None

        payload_hash = sha256(payload).hexdigest()
        cache_key = (kind, suffix, payload_hash)
        cached = self._payload_map.get(cache_key)
        if cached is not None:
            if cached.path.exists():
                return cached
            self._payload_map.pop(cache_key, None)

        attachment_id = self._reserve_id(dir_path, suffix)
        path = dir_path / attachment_id
        try:
            path.write_bytes(payload)
        except OSError as exc:
            logger.warning(
                "Failed to write cached attachment: {file} ({error})",
                file=path,
                error=exc,
            )
            return None

        cached = CachedAttachment(kind=kind, attachment_id=attachment_id, path=path)
        self._payload_map[cache_key] = cached
        return cached

    def store_image(self, image: Image.Image) -> CachedAttachment | None:
        """将 PIL 图片对象存储到缓存中。

        Args:
            image: PIL 图片对象。

        Returns:
            缓存附件对象，存储失败返回 None。
        """
        png_bytes = BytesIO()
        image.save(png_bytes, format="PNG")
        return self.store_bytes("image", ".png", png_bytes.getvalue())

    def _candidate_paths(self, kind: CachedAttachmentKind, attachment_id: str) -> list[Path]:
        """获取附件的可能路径列表（包括旧版路径）。

        Args:
            kind: 附件类型。
            attachment_id: 附件标识符。

        Returns:
            可能的文件路径列表。
        """
        roots = (self._root, *self._legacy_roots)
        return [self._dir_for(kind, root=root) / attachment_id for root in roots]

    def load_bytes(
        self, kind: CachedAttachmentKind, attachment_id: str
    ) -> tuple[Path, bytes] | None:
        """从缓存加载二进制数据。

        Args:
            kind: 附件类型。
            attachment_id: 附件标识符。

        Returns:
            文件路径和二进制数据的元组，加载失败返回 None。
        """
        for path in self._candidate_paths(kind, attachment_id):
            if not path.exists():
                continue
            try:
                return path, path.read_bytes()
            except OSError as exc:
                logger.warning(
                    "Failed to read cached attachment: {file} ({error})",
                    file=path,
                    error=exc,
                )
                return None
        return None

    def load_content_parts(
        self, kind: CachedAttachmentKind, attachment_id: str
    ) -> list[ContentPart] | None:
        """从缓存加载并构建内容部分列表。

        Args:
            kind: 附件类型。
            attachment_id: 附件标识符。

        Returns:
            内容部分列表，加载失败返回 None。
        """
        if kind == "image":
            payload = self.load_bytes(kind, attachment_id)
            if payload is None:
                return None
            path, image_bytes = payload
            mime_type = _guess_image_mime(path)
            part = _build_image_part(image_bytes, mime_type)
            return wrap_media_part(part, tag="image", attrs={"path": str(path)})
        return None


def parse_attachment_kind(raw_kind: str) -> CachedAttachmentKind | None:
    """解析原始字符串为附件类型。

    Args:
        raw_kind: 原始类型字符串。

    Returns:
        解析后的附件类型，无法解析返回 None。
    """
    if raw_kind == "image":
        return "image"
    return None


# 别名，供模块内部使用
_parse_attachment_kind = parse_attachment_kind


@dataclass(slots=True)
class PlaceholderTokenMatch:
    """占位符匹配结果数据类。

    Attributes:
        start: 匹配开始位置的索引。
        end: 匹配结束位置的索引。
        raw: 匹配的原始字符串。
        handler: 处理此占位符的处理器对象。
        match: 正则表达式匹配对象。
    """

    start: int
    end: int
    raw: str
    handler: PlaceholderHandler
    match: re.Match[str]


class PlaceholderHandler(Protocol):
    """占位符处理器协议。

    定义占位符处理器必须实现的方法接口。
    """

    def find_next(self, text: str, start: int = 0) -> PlaceholderTokenMatch | None:
        """在文本中查找下一个占位符匹配。

        Args:
            text: 要搜索的文本。
            start: 搜索起始位置。

        Returns:
            匹配结果，未找到返回 None。
        """
        ...

    def resolve_content(self, match: PlaceholderTokenMatch) -> list[ContentPart] | None:
        """将占位符解析为内容部分列表。

        Args:
            match: 占位符匹配结果。

        Returns:
            内容部分列表，解析失败返回 None。
        """
        ...

    def expand_text(self, match: PlaceholderTokenMatch) -> str | None:
        """将占位符展开为文本。

        Args:
            match: 占位符匹配结果。

        Returns:
            展开后的文本，无法展开返回 None。
        """
        ...

    def serialize_for_history(self, match: PlaceholderTokenMatch) -> str | None:
        """将占位符序列化为历史记录格式。

        Args:
            match: 占位符匹配结果。

        Returns:
            序列化后的字符串，无法序列化返回 None。
        """
        ...

    def expand_for_editor(self, match: PlaceholderTokenMatch) -> str | None:
        """将占位符展开为编辑器可显示的文本。

        Args:
            match: 占位符匹配结果。

        Returns:
            展开后的文本，无法展开返回 None。
        """
        ...


@dataclass(slots=True)
class PastedTextEntry:
    """粘贴文本条目数据类。

    Attributes:
        paste_id: 粘贴文本的唯一标识符。
        text: 粘贴的文本内容。
    """

    paste_id: int
    text: str

    @property
    def token(self) -> str:
        """获取此条目的占位符字符串。

        Returns:
            占位符字符串，如 ``[Pasted text #1 +10 lines]``。
        """
        return build_pasted_text_placeholder(self.paste_id, self.text)


class PastedTextPlaceholderHandler:
    """粘贴文本占位符处理器。

    管理粘贴文本的占位符创建、解析和编辑后重新折叠。

    Attributes:
        _entries: 粘贴文本条目的字典，键为 paste_id。
        _next_id: 下一个可用的 paste_id。
    """

    def __init__(self) -> None:
        """初始化粘贴文本占位符处理器。"""
        self._entries: dict[int, PastedTextEntry] = {}
        self._next_id = 1

    def create_placeholder(self, text: str) -> str:
        """为文本创建占位符。

        Args:
            text: 需要创建占位符的文本。

        Returns:
            创建的占位符字符串。
        """
        normalized = sanitize_surrogates(normalize_pasted_text(text))
        entry = PastedTextEntry(paste_id=self._next_id, text=normalized)
        self._entries[entry.paste_id] = entry
        self._next_id += 1
        return entry.token

    def maybe_placeholderize(self, text: str) -> str:
        """根据阈值判断是否将文本转换为占位符。

        Args:
            text: 需要判断的文本。

        Returns:
            如果符合条件返回占位符，否则返回规范化后的原文本。
        """
        normalized = normalize_pasted_text(text)
        if not should_placeholderize_pasted_text(normalized):
            return normalized
        return self.create_placeholder(normalized)

    def entry_for_id(self, paste_id: int) -> PastedTextEntry | None:
        """根据 ID 获取粘贴文本条目。

        Args:
            paste_id: 粘贴文本标识符。

        Returns:
            条目对象，不存在返回 None。
        """
        return self._entries.get(paste_id)

    def iter_entries_for_command(
        self, command: str
    ) -> list[tuple[PlaceholderTokenMatch, PastedTextEntry]]:
        """遍历命令中所有粘贴文本占位符及其对应的条目。

        Args:
            command: 包含占位符的命令字符串。

        Returns:
            匹配结果和条目的元组列表。
        """
        entries: list[tuple[PlaceholderTokenMatch, PastedTextEntry]] = []
        cursor = 0
        while match := self.find_next(command, cursor):
            paste_id = int(match.match.group("id"))
            entry = self.entry_for_id(paste_id)
            if entry is not None:
                entries.append((match, entry))
            cursor = match.end
        return entries

    def find_next(self, text: str, start: int = 0) -> PlaceholderTokenMatch | None:
        """在文本中查找下一个粘贴文本占位符。

        Args:
            text: 要搜索的文本。
            start: 搜索起始位置。

        Returns:
            匹配结果，未找到返回 None。
        """
        match = _PASTED_TEXT_PLACEHOLDER_RE.search(text, start)
        if match is None:
            return None
        return PlaceholderTokenMatch(
            start=match.start(),
            end=match.end(),
            raw=match.group(0),
            handler=self,
            match=match,
        )

    def resolve_content(self, match: PlaceholderTokenMatch) -> list[ContentPart] | None:
        """将粘贴文本占位符解析为内容部分。

        Args:
            match: 占位符匹配结果。

        Returns:
            内容部分列表，解析失败返回 None。
        """
        paste_id = int(match.match.group("id"))
        entry = self.entry_for_id(paste_id)
        if entry is None:
            return None
        return [TextPart(text=entry.text)]

    def expand_text(self, match: PlaceholderTokenMatch) -> str | None:
        """将粘贴文本占位符展开为实际文本。

        Args:
            match: 占位符匹配结果。

        Returns:
            展开后的文本，条目不存在返回 None。
        """
        paste_id = int(match.match.group("id"))
        entry = self.entry_for_id(paste_id)
        return None if entry is None else entry.text

    def serialize_for_history(self, match: PlaceholderTokenMatch) -> str | None:
        """将占位符序列化为历史记录格式（展开为实际文本）。

        Args:
            match: 占位符匹配结果。

        Returns:
            展开后的文本。
        """
        return self.expand_text(match)

    def expand_for_editor(self, match: PlaceholderTokenMatch) -> str | None:
        """将占位符展开为编辑器可显示的文本。

        Args:
            match: 占位符匹配结果。

        Returns:
            展开后的文本。
        """
        return self.expand_text(match)

    def refold_after_editor(self, edited_text: str, original_command: str) -> str:
        """在编辑器编辑后重新折叠占位符。

        使用序列匹配算法检测编辑后文本中未被修改的占位符内容，
        将其重新折叠为占位符格式。

        Args:
            edited_text: 编辑后的文本。
            original_command: 原始命令字符串。

        Returns:
            重新折叠后的文本。
        """
        expanded_original, intervals = self._expanded_text_and_intervals(original_command)
        if not intervals:
            return edited_text

        opcodes = SequenceMatcher(
            a=expanded_original,
            b=edited_text,
            autojunk=False,
        ).get_opcodes()
        replacements: list[tuple[int, int, str]] = []
        for start, end, token, expected_text in intervals:
            mapped = self._map_interval(opcodes, start, end)
            if mapped is None:
                continue
            mapped_start, mapped_end = mapped
            if edited_text[mapped_start:mapped_end] != expected_text:
                continue
            replacements.append((mapped_start, mapped_end, token))

        result = edited_text
        for start, end, token in reversed(replacements):
            result = result[:start] + token + result[end:]
        return result

    def _expanded_text_and_intervals(
        self, command: str
    ) -> tuple[str, list[tuple[int, int, str, str]]]:
        """展开命令中的所有占位符并记录位置区间。

        Args:
            command: 包含占位符的命令字符串。

        Returns:
            展开后的文本和位置区间列表的元组。
        """
        parts: list[str] = []
        intervals: list[tuple[int, int, str, str]] = []
        cursor = 0
        expanded_cursor = 0
        for match, entry in self.iter_entries_for_command(command):
            literal = command[cursor : match.start]
            if literal:
                parts.append(literal)
                expanded_cursor += len(literal)
            start = expanded_cursor
            parts.append(entry.text)
            expanded_cursor += len(entry.text)
            intervals.append((start, expanded_cursor, match.raw, entry.text))
            cursor = match.end
        if cursor < len(command):
            parts.append(command[cursor:])
        return "".join(parts), intervals

    @staticmethod
    def _map_interval(
        opcodes: Sequence[tuple[str, int, int, int, int]], start: int, end: int
    ) -> tuple[int, int] | None:
        """使用操作码序列将原始文本区间映射到编辑后文本。

        Args:
            opcodes: SequenceMatcher 生成的操作码序列。
            start: 原始区间的起始位置。
            end: 原始区间的结束位置。

        Returns:
            映射后的位置区间，无法完整映射返回 None。
        """
        mapped_start: int | None = None
        mapped_end: int | None = None
        cursor = start
        for tag, i1, i2, j1, _j2 in opcodes:
            if i2 <= cursor:
                continue
            if i1 >= end:
                break
            overlap_start = max(i1, cursor, start)
            overlap_end = min(i2, end)
            if overlap_start >= overlap_end:
                continue
            if tag != "equal":
                return None
            segment_start = j1 + (overlap_start - i1)
            segment_end = j1 + (overlap_end - i1)
            if mapped_start is None:
                mapped_start = segment_start
            elif mapped_end != segment_start:
                return None
            mapped_end = segment_end
            cursor = overlap_end
        if cursor != end or mapped_start is None or mapped_end is None:
            return None
        return mapped_start, mapped_end


class ImagePlaceholderHandler:
    """图片占位符处理器。

    管理图片占位符的创建和解析。

    Attributes:
        _attachment_cache: 附件缓存对象。
    """

    def __init__(self, attachment_cache: AttachmentCache) -> None:
        """初始化图片占位符处理器。

        Args:
            attachment_cache: 附件缓存对象，用于存储图片数据。
        """
        self._attachment_cache = attachment_cache

    def create_placeholder(self, image: Image.Image) -> str | None:
        """为图片创建占位符。

        Args:
            image: PIL 图片对象。

        Returns:
            占位符字符串，格式如 ``[image:abc123,800x600]``，存储失败返回 None。
        """
        cached = self._attachment_cache.store_image(image)
        if cached is None:
            return None
        return f"[image:{cached.attachment_id},{image.width}x{image.height}]"

    def find_next(self, text: str, start: int = 0) -> PlaceholderTokenMatch | None:
        """在文本中查找下一个图片占位符。

        Args:
            text: 要搜索的文本。
            start: 搜索起始位置。

        Returns:
            匹配结果，未找到返回 None。
        """
        match = _IMAGE_PLACEHOLDER_RE.search(text, start)
        if match is None:
            return None
        return PlaceholderTokenMatch(
            start=match.start(),
            end=match.end(),
            raw=match.group(0),
            handler=self,
            match=match,
        )

    def resolve_content(self, match: PlaceholderTokenMatch) -> list[ContentPart] | None:
        """将图片占位符解析为内容部分。

        Args:
            match: 占位符匹配结果。

        Returns:
            内容部分列表，解析失败返回 None。
        """
        kind = parse_attachment_kind(match.match.group("type"))
        if kind is None:
            return None
        return self._attachment_cache.load_content_parts(kind, match.match.group("id"))

    def expand_text(self, match: PlaceholderTokenMatch) -> str | None:
        """将图片占位符展开为文本（保持原样）。

        Args:
            match: 占位符匹配结果。

        Returns:
            原始占位符字符串。
        """
        return match.raw

    def serialize_for_history(self, match: PlaceholderTokenMatch) -> str | None:
        """将占位符序列化为历史记录格式。

        Args:
            match: 占位符匹配结果。

        Returns:
            原始占位符字符串。
        """
        return match.raw

    def expand_for_editor(self, match: PlaceholderTokenMatch) -> str | None:
        """将占位符展开为编辑器可显示的文本。

        Args:
            match: 占位符匹配结果。

        Returns:
            原始占位符字符串。
        """
        return match.raw


@dataclass(slots=True)
class ResolvedPromptCommand:
    """解析后的提示词命令数据类。

    Attributes:
        display_command: 用于显示的命令字符串（含占位符）。
        resolved_text: 解析后的文本内容（占位符已展开）。
        content: 内容部分列表，包含文本和图片等。
    """

    display_command: str
    resolved_text: str
    content: list[ContentPart]


class PromptPlaceholderManager:
    """提示词占位符管理器。

    统一管理粘贴文本和图片占位符，提供创建、解析和序列化功能。

    Attributes:
        _attachment_cache: 附件缓存对象。
        _text_handler: 粘贴文本占位符处理器。
        _image_handler: 图片占位符处理器。
        _handlers: 所有占位符处理器列表。
    """

    def __init__(self, attachment_cache: AttachmentCache | None = None) -> None:
        """初始化提示词占位符管理器。

        Args:
            attachment_cache: 可选的附件缓存对象，默认自动创建。
        """
        self._attachment_cache = attachment_cache or AttachmentCache()
        self._text_handler = PastedTextPlaceholderHandler()
        self._image_handler = ImagePlaceholderHandler(self._attachment_cache)
        self._handlers: tuple[PlaceholderHandler, ...] = (
            self._text_handler,
            self._image_handler,
        )

    @property
    def attachment_cache(self) -> AttachmentCache:
        """获取附件缓存对象。

        Returns:
            附件缓存对象。
        """
        return self._attachment_cache

    def maybe_placeholderize_pasted_text(self, text: str) -> str:
        """根据阈值判断是否将粘贴文本转换为占位符。

        Args:
            text: 需要判断的文本。

        Returns:
            如果符合条件返回占位符，否则返回规范化后的原文本。
        """
        return self._text_handler.maybe_placeholderize(text)

    def create_image_placeholder(self, image: Image.Image) -> str | None:
        """为图片创建占位符。

        Args:
            image: PIL 图片对象。

        Returns:
            占位符字符串，存储失败返回 None。
        """
        return self._image_handler.create_placeholder(image)

    def resolve_command(self, command: str) -> ResolvedPromptCommand:
        """解析包含占位符的命令字符串。

        Args:
            command: 包含占位符的命令字符串。

        Returns:
            解析后的命令对象。
        """
        content: list[ContentPart] = []
        resolved_chunks: list[str] = []
        cursor = 0

        while match := self._find_next_match(command, cursor):
            if match.start > cursor:
                literal = command[cursor : match.start]
                content.append(TextPart(text=literal))
                resolved_chunks.append(literal)

            resolved_content = match.handler.resolve_content(match)
            if resolved_content is None:
                content.append(TextPart(text=match.raw))
                resolved_chunks.append(match.raw)
            else:
                content.extend(resolved_content)
                expanded = match.handler.expand_text(match)
                resolved_chunks.append(match.raw if expanded is None else expanded)

            cursor = match.end

        if cursor < len(command):
            literal = command[cursor:]
            content.append(TextPart(text=literal))
            resolved_chunks.append(literal)

        return ResolvedPromptCommand(
            display_command=command,
            resolved_text="".join(resolved_chunks),
            content=content,
        )

    def serialize_for_history(self, command: str) -> str:
        """将命令序列化为历史记录格式。

        Args:
            command: 包含占位符的命令字符串。

        Returns:
            序列化后的命令字符串。
        """
        return self._rewrite_command(
            command,
            lambda handler, match: handler.serialize_for_history(match),
        )

    def expand_for_editor(self, command: str) -> str:
        """将命令展开为编辑器可显示的格式。

        Args:
            command: 包含占位符的命令字符串。

        Returns:
            展开后的命令字符串。
        """
        return self._rewrite_command(
            command,
            lambda handler, match: handler.expand_for_editor(match),
        )

    def refold_after_editor(self, edited_text: str, original_command: str) -> str:
        """在编辑器编辑后重新折叠占位符。

        Args:
            edited_text: 编辑后的文本。
            original_command: 原始命令字符串。

        Returns:
            重新折叠后的文本。
        """
        return self._text_handler.refold_after_editor(edited_text, original_command)

    def _find_next_match(self, text: str, start: int = 0) -> PlaceholderTokenMatch | None:
        """在文本中查找下一个任意类型的占位符。

        Args:
            text: 要搜索的文本。
            start: 搜索起始位置。

        Returns:
            最早出现的匹配结果，未找到返回 None。
        """
        earliest: PlaceholderTokenMatch | None = None
        for handler in self._handlers:
            match = handler.find_next(text, start)
            if match is None:
                continue
            if earliest is None or match.start < earliest.start:
                earliest = match
        return earliest

    def _rewrite_command(
        self,
        command: str,
        replacer: Callable[[PlaceholderHandler, PlaceholderTokenMatch], str | None],
    ) -> str:
        """使用替换函数重写命令中的占位符。

        Args:
            command: 包含占位符的命令字符串。
            replacer: 替换函数，接收处理器和匹配结果，返回替换文本。

        Returns:
            重写后的命令字符串。
        """
        parts: list[str] = []
        cursor = 0

        while match := self._find_next_match(command, cursor):
            if match.start > cursor:
                parts.append(command[cursor : match.start])
            replacement = replacer(match.handler, match)
            parts.append(match.raw if replacement is None else replacement)
            cursor = match.end

        if cursor < len(command):
            parts.append(command[cursor:])

        return "".join(parts)
