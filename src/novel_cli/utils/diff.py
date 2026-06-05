"""diff 工具模块。

提供文本差异比较和格式化功能，用于生成统一的 diff 输出以及构建可展示的 diff 区块。
"""

from __future__ import annotations

import asyncio
import difflib
from difflib import SequenceMatcher

from kosong.tooling import DisplayBlock

from novel_cli.tools.display import DiffDisplayBlock

N_CONTEXT_LINES = 3

_HUGE_FILE_THRESHOLD = 10000
"""超过此行数阈值时跳过 diff 计算。"""


def format_unified_diff(
    old_text: str,
    new_text: str,
    path: str = "",
    *,
    include_file_header: bool = True,
) -> str:
    """格式化 old_text 与 new_text 之间的统一 diff。

    Args:
        old_text: 原始文本内容。
        new_text: 修改后的文本内容。
        path: 可选的文件路径，用于 diff 头部显示。
        include_file_header: 是否包含 ---/+++ 文件头行。

    Returns:
        统一格式的 diff 字符串。
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    # 确保行以换行符结尾，以正确格式化 diff
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    fromfile = f"a/{path}" if path else "a/file"
    tofile = f"b/{path}" if path else "b/file"

    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="\n",
        )
    )

    if (
        not include_file_header
        and len(diff) >= 2
        and diff[0].startswith("--- ")
        and diff[1].startswith("+++ ")
    ):
        diff = diff[2:]

    return "".join(diff)


def _build_diff_blocks_sync(
    path: str,
    old_text: str,
    new_text: str,
) -> list[DisplayBlock]:
    """同步构建 diff 区块。

    CPU 密集型操作，设计为在线程中运行。

    Args:
        path: 文件路径。
        old_text: 原始文本内容。
        new_text: 修改后的文本内容。

    Returns:
        DisplayBlock 列表，每个区块包含上下文窗口内的差异内容。
    """
    if old_text == new_text:
        return []

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    max_lines = max(len(old_lines), len(new_lines))

    # 大文件：跳过 diff 计算，返回摘要区块
    if max_lines > _HUGE_FILE_THRESHOLD:
        old_desc = f"({len(old_lines)} lines)"
        if len(old_lines) == len(new_lines):
            new_desc = f"({len(new_lines)} lines, modified)"
        else:
            new_desc = f"({len(new_lines)} lines)"
        return [
            DiffDisplayBlock(
                path=path,
                old_text=old_desc,
                new_text=new_desc,
                old_start=1,
                new_start=1,
                is_summary=True,
            )
        ]

    matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)

    blocks: list[DisplayBlock] = []
    for group in matcher.get_grouped_opcodes(n=N_CONTEXT_LINES):
        if not group:
            continue
        i1 = group[0][1]
        i2 = group[-1][2]
        j1 = group[0][3]
        j2 = group[-1][4]
        blocks.append(
            DiffDisplayBlock(
                path=path,
                old_text="\n".join(old_lines[i1:i2]),
                new_text="\n".join(new_lines[j1:j2]),
                old_start=i1 + 1,
                new_start=j1 + 1,
            )
        )
    return blocks


async def build_diff_blocks(
    path: str,
    old_text: str,
    new_text: str,
) -> list[DisplayBlock]:
    """构建带有上下文窗口的 diff 展示区块。

    将 CPU 密集型的 diff 计算放入线程中执行，避免阻塞事件循环。

    Args:
        path: 文件路径。
        old_text: 原始文本内容。
        new_text: 修改后的文本内容。

    Returns:
        DisplayBlock 列表，每个区块包含上下文窗口内的差异内容。
    """
    if old_text == new_text:
        return []
    return await asyncio.to_thread(_build_diff_blocks_sync, path, old_text, new_text)