"""文件工具辅助模块。

提供文件类型检测、MIME 类型识别等辅助功能。
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal

MEDIA_SNIFF_BYTES = 512

# 额外的 MIME 类型映射（补充 mimetypes 默认不支持的类型）
_EXTRA_MIME_TYPES = {
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".mkv": "video/x-matroska",
    ".m4v": "video/x-m4v",
    ".3gp": "video/3gpp",
    ".3g2": "video/3gpp2",
    # TypeScript 文件：覆盖 mimetypes 默认值（video/mp2t 用于 MPEG Transport Stream）
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".mts": "text/typescript",
    ".cts": "text/typescript",
}

for suffix, mime_type in _EXTRA_MIME_TYPES.items():
    mimetypes.add_type(mime_type, suffix)

# 图片 MIME 类型按后缀映射
_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".avif": "image/avif",
    ".svgz": "image/svg+xml",
}
# 视频 MIME 类型按后缀映射
_VIDEO_MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".wmv": "video/x-ms-wmv",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
    ".3g2": "video/3gpp2",
}
# 文本 MIME 类型按后缀映射
_TEXT_MIME_BY_SUFFIX = {
    ".svg": "image/svg+xml",
}

# ASF 文件头标识
_ASF_HEADER = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"
# FTYP 图片品牌映射
_FTYP_IMAGE_BRANDS = {
    "avif": "image/avif",
    "avis": "image/avif",
    "heic": "image/heic",
    "heif": "image/heif",
    "heix": "image/heif",
    "hevc": "image/heic",
    "mif1": "image/heif",
    "msf1": "image/heif",
}
# FTYP 视频品牌映射
_FTYP_VIDEO_BRANDS = {
    "isom": "video/mp4",
    "iso2": "video/mp4",
    "iso5": "video/mp4",
    "mp41": "video/mp4",
    "mp42": "video/mp4",
    "avc1": "video/mp4",
    "mp4v": "video/mp4",
    "m4v": "video/x-m4v",
    "qt": "video/quicktime",
    "3gp4": "video/3gpp",
    "3gp5": "video/3gpp",
    "3gp6": "video/3gpp",
    "3gp7": "video/3gpp",
    "3g2": "video/3gpp2",
}

# 非文本文件后缀集合（二进制文件、文档、压缩包等）
_NON_TEXT_SUFFIXES = {
    ".icns",
    ".psd",
    ".ai",
    ".eps",
    # 文档 /办公格式
    ".pdf",
    ".doc",
    ".docx",
    ".dot",
    ".dotx",
    ".rtf",
    ".odt",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".xlt",
    ".xltx",
    ".xltm",
    ".ods",
    ".ppt",
    ".pptx",
    ".pptm",
    ".pps",
    ".ppsx",
    ".odp",
    ".pages",
    ".numbers",
    ".key",
    # 压缩包 /压缩文件
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".zst",
    ".lz",
    ".lz4",
    ".br",
    ".cab",
    ".ar",
    ".deb",
    ".rpm",
    # 音频
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".aac",
    ".m4a",
    ".wma",
    # 字体
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    # 二进制 /打包文件
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".apk",
    ".ipa",
    ".jar",
    ".class",
    ".pyc",
    ".pyo",
    ".wasm",
    # 磁盘镜像 /数据库
    ".dmg",
    ".iso",
    ".img",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db3",
}


@dataclass(frozen=True)
class FileType:
    """文件类型信息数据类。

    Attributes:
        kind: 文件类型类别，可选值为 text、image、video 或 unknown。
        mime_type: MIME 类型字符串。
    """

    kind: Literal["text", "image", "video", "unknown"]
    mime_type: str


def _sniff_ftyp_brand(header: bytes) -> str | None:
    """从文件头中嗅探 FTYP 品牌。

    Args:
        header: 文件头字节数据。

    Returns:
        FTYP 品牌字符串，如果无法识别则返回 None。
    """
    if len(header) < 12 or header[4:8] != b"ftyp":
        return None
    brand = header[8:12].decode("ascii", errors="ignore").lower()
    return brand.strip()


def sniff_media_from_magic(data: bytes) -> FileType | None:
    """通过魔数嗅探媒体文件类型。

    根据文件头部的魔数字节识别图片和视频文件类型。

    Args:
        data: 文件数据字节。

    Returns:
        识别到的 FileType 对象，如果无法识别则返回 None。
    """
    header = data[:MEDIA_SNIFF_BYTES]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileType(kind="image", mime_type="image/png")
    if header.startswith(b"\xff\xd8\xff"):
        return FileType(kind="image", mime_type="image/jpeg")
    if header.startswith((b"GIF87a", b"GIF89a")):
        return FileType(kind="image", mime_type="image/gif")
    if header.startswith(b"BM"):
        return FileType(kind="image", mime_type="image/bmp")
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return FileType(kind="image", mime_type="image/tiff")
    if header.startswith(b"\x00\x00\x01\x00"):
        return FileType(kind="image", mime_type="image/x-icon")
    if header.startswith(b"RIFF") and len(header) >= 12:
        chunk = header[8:12]
        if chunk == b"WEBP":
            return FileType(kind="image", mime_type="image/webp")
        if chunk == b"AVI ":
            return FileType(kind="video", mime_type="video/x-msvideo")
    if header.startswith(b"FLV"):
        return FileType(kind="video", mime_type="video/x-flv")
    if header.startswith(_ASF_HEADER):
        return FileType(kind="video", mime_type="video/x-ms-wmv")
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        lowered = header.lower()
        if b"webm" in lowered:
            return FileType(kind="video", mime_type="video/webm")
        if b"matroska" in lowered:
            return FileType(kind="video", mime_type="video/x-matroska")
    if brand := _sniff_ftyp_brand(header):
        if brand in _FTYP_IMAGE_BRANDS:
            return FileType(kind="image", mime_type=_FTYP_IMAGE_BRANDS[brand])
        if brand in _FTYP_VIDEO_BRANDS:
            return FileType(kind="video", mime_type=_FTYP_VIDEO_BRANDS[brand])
    return None


def detect_file_type(path: str | PurePath, header: bytes | None = None) -> FileType:
    """检测文件类型。

    通过文件后缀和文件头综合判断文件类型，支持文本、图片、视频等类型识别。

    Args:
        path: 文件路径。
        header: 可选的文件头字节数据，用于更准确的类型检测。

    Returns:
        FileType 对象，包含文件类型类别和 MIME 类型。
    """
    suffix = PurePath(str(path)).suffix.lower()
    media_hint: FileType | None = None
    if suffix in _TEXT_MIME_BY_SUFFIX:
        media_hint = FileType(kind="text", mime_type=_TEXT_MIME_BY_SUFFIX[suffix])
    elif suffix in _IMAGE_MIME_BY_SUFFIX:
        media_hint = FileType(kind="image", mime_type=_IMAGE_MIME_BY_SUFFIX[suffix])
    elif suffix in _VIDEO_MIME_BY_SUFFIX:
        media_hint = FileType(kind="video", mime_type=_VIDEO_MIME_BY_SUFFIX[suffix])
    else:
        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type:
            if mime_type.startswith("image/"):
                media_hint = FileType(kind="image", mime_type=mime_type)
            elif mime_type.startswith("video/"):
                media_hint = FileType(kind="video", mime_type=mime_type)

    if media_hint and media_hint.kind in ("image", "video"):
        return media_hint

    if header is not None:
        sniffed = sniff_media_from_magic(header)
        if sniffed:
            if media_hint and sniffed.kind != media_hint.kind:
                return FileType(kind="unknown", mime_type="")
            return sniffed
        # NUL 字节是二进制内容的强信号
        if b"\x00" in header:
            return FileType(kind="unknown", mime_type="")

    if media_hint:
        return media_hint
    if suffix in _NON_TEXT_SUFFIXES:
        return FileType(kind="unknown", mime_type="")
    return FileType(kind="text", mime_type="text/plain")
