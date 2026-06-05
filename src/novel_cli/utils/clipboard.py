"""剪贴板媒体读取模块。

提供从系统剪贴板读取图片和文件路径的功能，支持跨平台操作。
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyperclip
from PIL import Image, ImageGrab

# 剪贴板粘贴时识别的视频文件扩展名
_VIDEO_SUFFIXES: frozenset[str] = frozenset(
    {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".flv", ".3gp", ".3g2"}
)


@dataclass(frozen=True, slots=True)
class ClipboardResult:
    """从剪贴板读取媒体的结果。

    当剪贴板包含图片文件和非图片文件（视频、PDF 等）的混合时，
    两个字段可能同时非空。

    Attributes:
        images: 加载的 PIL 图片元组。
        file_paths: 非图片文件的路径元组。
    """

    images: tuple[Image.Image, ...]
    file_paths: tuple[Path, ...]


def is_clipboard_available() -> bool:
    """检查 Pyperclip 剪贴板是否可用。

    Returns:
        剪贴板可用返回 True，否则返回 False。
    """
    try:
        pyperclip.paste()
        return True
    except Exception:
        return False


def grab_media_from_clipboard() -> ClipboardResult | None:
    """从剪贴板读取媒体。

    检查剪贴板一次并返回所有检测到的媒体。
    图片文件作为已加载的 PIL 图片返回；非图片文件（视频、PDF 等）
    作为文件路径返回。

    在 macOS 上优先尝试原生 pasteboard API，以避免将文件缩略图
    误识别为剪贴板图片数据。

    Returns:
        ClipboardResult 包含图片和文件路径，若剪贴板无媒体则返回 None。
    """
    # 1. 尝试 macOS 原生 API 获取文件路径（对 Finder 复制最可靠）
    if sys.platform == "darwin":
        file_paths = _read_clipboard_file_paths_macos_native()
        images, non_image_paths = _classify_file_paths(file_paths)
        if images or non_image_paths:
            return ClipboardResult(
                images=tuple(images),
                file_paths=tuple(non_image_paths),
            )

    # 2. 尝试 PIL ImageGrab 作为回退方案
    #    - 在 macOS 上使用 AppleScript «class furl» 获取文件路径，
    #      或从 pasteboard 读取原始图片数据（TIFF/PNG）。
    #    - 在其他平台上这是主要的剪贴板访问方式。
    payload = ImageGrab.grabclipboard()
    if payload is None:
        return None
    if isinstance(payload, Image.Image):
        # 原始图片数据（截图或缩略图）
        # 如果执行到这里，说明 macOS 原生路径查找没有找到任何文件路径，
        # 因此可以安全地将其视为真实图片。
        return ClipboardResult(images=(payload,), file_paths=())
    # payload 是文件路径字符串列表
    images, non_image_paths = _classify_file_paths(payload)
    if images or non_image_paths:
        return ClipboardResult(
            images=tuple(images),
            file_paths=tuple(non_image_paths),
        )
    return None


def _classify_file_paths(
    paths: Iterable[os.PathLike[str] | str],
) -> tuple[list[Image.Image], list[Path]]:
    """将剪贴板文件路径分类为图片和非图片文件。

    Args:
        paths: 待分类的路径可迭代对象。

    Returns:
        元组 (images, non_image_paths)，其中 images 包含已加载的 PIL 图片，
        non_image_paths 包含视频、文档等非图片文件路径。
    """
    resolved: list[Path] = []
    for item in paths:
        try:
            path = Path(item)
        except (TypeError, ValueError):
            continue
        if not path.is_file():
            continue
        resolved.append(path)

    images: list[Image.Image] = []
    non_image_paths: list[Path] = []

    for path in resolved:
        # 视频文件永远不会作为图片打开
        if path.suffix.lower() in _VIDEO_SUFFIXES:
            non_image_paths.append(path)
            continue
        try:
            with Image.open(path) as img:
                img.load()
                images.append(img.copy())
        except Exception:
            non_image_paths.append(path)

    return images, non_image_paths


def _read_clipboard_file_paths_macos_native() -> list[Path]:
    """使用 macOS 原生 API 读取剪贴板中的文件路径。

    Returns:
        文件路径列表，若无法读取则返回空列表。
    """
    try:
        appkit = cast(Any, importlib.import_module("AppKit"))
        foundation = cast(Any, importlib.import_module("Foundation"))
    except Exception:
        return []

    NSPasteboard = appkit.NSPasteboard
    NSURL = foundation.NSURL
    options_key = getattr(
        appkit,
        "NSPasteboardURLReadingFileURLsOnlyKey",
        "NSPasteboardURLReadingFileURLsOnlyKey",
    )

    pb = NSPasteboard.generalPasteboard()
    options = {options_key: True}
    try:
        urls: list[Any] | None = pb.readObjectsForClasses_options_([NSURL], options)
    except Exception:
        urls = None

    paths: list[Path] = []
    if urls:
        for url in urls:
            try:
                path = url.path()
            except Exception:
                continue
            if path:
                paths.append(Path(str(path)))

    if paths:
        return paths

    try:
        file_list = cast(list[str] | str | None, pb.propertyListForType_("NSFilenamesPboardType"))
    except Exception:
        return []

    if not file_list:
        return []

    file_items: list[str] = []
    if isinstance(file_list, list):
        file_items.extend(item for item in file_list if item)
    else:
        file_items.append(file_list)

    return [Path(item) for item in file_items]
