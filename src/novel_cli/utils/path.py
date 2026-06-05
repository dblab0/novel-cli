"""路径处理工具模块。

提供文件路径操作、目录列表生成、工作区路径检查等功能。
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Sequence
from pathlib import Path, PurePath
from stat import S_ISDIR

import aiofiles.os
from kaos.path import KaosPath

_ROTATION_OPEN_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY
_ROTATION_FILE_MODE = 0o600


async def _reserve_rotation_path(path: Path) -> bool:
    """原子性地创建空文件作为 *path* 的预留占位符。

    Args:
        path: 预留的文件路径。

    Returns:
        成功创建返回 True，若文件已存在则返回 False。
    """

    def _create() -> None:
        fd = os.open(str(path), _ROTATION_OPEN_FLAGS, _ROTATION_FILE_MODE)
        os.close(fd)

    try:
        await asyncio.to_thread(_create)
    except FileExistsError:
        return False
    return True


async def next_available_rotation(path: Path) -> Path | None:
    """返回 *path* 的预留轮转路径，若父目录不存在则返回 None。

    调用者必须立即覆盖/复用返回的路径，因为此辅助函数会提交一个
    空占位文件来保证唯一性。因此它适用于轮转*文件*（如历史日志），
    但**不**适用于目录创建。

    Args:
        path: 基础文件路径。

    Returns:
        预留的轮转路径，若父目录不存在则返回 None。
    """

    if not path.parent.exists():
        return None

    base_name = path.stem
    suffix = path.suffix
    pattern = re.compile(rf"^{re.escape(base_name)}_(\d+){re.escape(suffix)}$")
    max_num = 0
    for entry in await aiofiles.os.listdir(path.parent):
        if match := pattern.match(entry):
            max_num = max(max_num, int(match.group(1)))

    next_num = max_num + 1
    while True:
        next_path = path.parent / f"{base_name}_{next_num}{suffix}"
        if await _reserve_rotation_path(next_path):
            return next_path
        next_num += 1


async def list_directory(work_dir: KaosPath) -> str:
    """返回 *work_dir* 的类似 ``ls`` 的目录列表。

    此辅助函数主要用于为 LLM 提供上下文（例如 ``NOVEL_WORK_DIR_LS``），
    以及在工具中显示顶层目录内容。因此它需要对单个条目的文件系统问题
    具有鲁棒性：单个损坏的条目不应导致整个 CLI 崩溃。

    Args:
        work_dir: 待列出的工作目录。

    Returns:
        格式化的目录列表字符串。
    """

    entries: list[str] = []
    # 遍历条目；容忍单个条目的 stat 失败（损坏的符号链接、权限错误等）
    async for entry in work_dir.iterdir():
        try:
            st = await entry.stat()
        except OSError:
            # 损坏的符号链接、权限错误等 — 继续列出其他条目
            entries.append(f"?--------- {'?':>10} {entry.name} [stat failed]")
            continue
        mode = "d" if S_ISDIR(st.st_mode) else "-"
        mode += "r" if st.st_mode & 0o400 else "-"
        mode += "w" if st.st_mode & 0o200 else "-"
        mode += "x" if st.st_mode & 0o100 else "-"
        mode += "r" if st.st_mode & 0o040 else "-"
        mode += "w" if st.st_mode & 0o020 else "-"
        mode += "x" if st.st_mode & 0o010 else "-"
        mode += "r" if st.st_mode & 0o004 else "-"
        mode += "w" if st.st_mode & 0o002 else "-"
        mode += "x" if st.st_mode & 0o001 else "-"
        entries.append(f"{mode} {st.st_size:>10} {entry.name}")
    return "\n".join(entries)


def shorten_home(path: KaosPath) -> KaosPath:
    """将绝对路径转换为使用 `~` 表示主目录。

    Args:
        path: 待转换的路径。

    Returns:
        使用 `~` 缩写的路径，若无法转换则返回原路径。
    """
    try:
        home = KaosPath.home()
        p = path.relative_to(home)
        return KaosPath("~") / p
    except Exception:
        return path


def sanitize_cli_path(raw: str) -> str:
    """移除 CLI 路径参数两端的引号。

    在 macOS 上，将文件拖入终端会用单引号包裹路径
    （例如 ``'/path/to/file'``）。此辅助函数移除匹配的外层引号
    （单引号或双引号），以便下游路径处理正常工作。

    Args:
        raw: 原始路径字符串。

    Returns:
        去除两端引号后的路径字符串。
    """
    raw = raw.strip()
    if len(raw) >= 2 and ((raw[0] == "'" and raw[-1] == "'") or (raw[0] == '"' and raw[-1] == '"')):
        raw = raw[1:-1]
    return raw


def is_within_directory(path: KaosPath, directory: KaosPath) -> bool:
    """检查 *path* 是否包含在 *directory* 内，使用纯路径语义。

    两个参数应已经规范化（例如通过 KaosPath.canonical()）。

    Args:
        path: 待检查的路径。
        directory: 目标目录。

    Returns:
        若路径在目录内返回 True，否则返回 False。
    """
    candidate = PurePath(str(path))
    base = PurePath(str(directory))
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def is_within_workspace(
    path: KaosPath,
    work_dir: KaosPath,
    additional_dirs: Sequence[KaosPath] = (),
) -> bool:
    """检查 *path* 是否在工作区内（work_dir 或任何附加目录）。

    Args:
        path: 待检查的路径。
        work_dir: 工作目录。
        additional_dirs: 附加的允许目录序列。

    Returns:
        若路径在工作区内返回 True，否则返回 False。
    """
    if is_within_directory(path, work_dir):
        return True
    return any(is_within_directory(path, d) for d in additional_dirs)
