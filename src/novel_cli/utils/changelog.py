"""更新日志解析模块。

解析和格式化 Keep a Changelog 风格的 Markdown 更新日志。
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class ReleaseEntry(NamedTuple):
    """版本发布条目。

    Attributes:
        description: 版本描述文本。
        entries: 该版本的更新条目列表。
    """

    description: str
    entries: list[str]


def parse_changelog(md_text: str) -> dict[str, ReleaseEntry]:
    """解析 Keep a Changelog 风格的 Markdown 更新日志。

    解析规则：
    - 版本由以 '## [' 开头的二级标题标识。
      例如：`## [v0.10.1] - 2025-09-18` 或 `## [Unreleased]`
    - 每个版本区块的描述是该标题后第一段不以 '-' 或 '#' 开头的连续非空行。
    - 条目是该版本下所有以 '- ' 开头的 Markdown 列表项
      （跨任意子标题如 '### Added'）。

    Args:
        md_text: Markdown 格式的更新日志文本。

    Returns:
        版本号到 ReleaseEntry 的映射字典。
    """
    lines = md_text.splitlines()
    result: dict[str, ReleaseEntry] = {}

    current_ver: str | None = None
    collecting_desc = False
    desc_lines: list[str] = []
    bullet_lines: list[str] = []
    seen_content_after_header = False

    def commit():
        """提交当前版本的数据到结果字典。"""
        nonlocal current_ver, desc_lines, bullet_lines, result
        if current_ver is None:
            return
        description = "\n".join([line.strip() for line in desc_lines]).strip()
        # 去重并规范化条目
        norm_entries = [
            line.strip()[2:].strip() for line in bullet_lines if line.strip().startswith("- ")
        ]
        result[current_ver] = ReleaseEntry(description=description, entries=norm_entries)

    for raw in lines:
        line = raw.rstrip()
        # 格式：`## 0.75 (2026-01-09)` 或 `## Unreleased`
        if line.startswith("## "):
            commit()
            ver = line[3:].strip()
            # 移除括号中的日期（如果存在）
            if "(" in ver:
                ver = ver[: ver.find("(")].strip()
            current_ver = ver
            desc_lines = []
            bullet_lines = []
            collecting_desc = True
            seen_content_after_header = False
            continue

        if current_ver is None:
            # 跳过直到第一个版本区块
            continue

        if not line.strip():
            # 空行仅在已有内容后结束初始描述区块
            if collecting_desc and seen_content_after_header:
                collecting_desc = False
            continue

        seen_content_after_header = True

        if line.lstrip().startswith("### "):
            collecting_desc = False
            continue

        if line.lstrip().startswith("- "):
            collecting_desc = False
            bullet_lines.append(line.strip())
            continue

        if collecting_desc:
            # 累积描述直到空行或列表/子标题
            desc_lines.append(line.strip())
        # 其他情况：忽略描述区块后的任意自由格式文本

    # 最终提交
    commit()
    return result


def format_release_notes(changelog: dict[str, ReleaseEntry], include_lib_changes: bool) -> str:
    """格式化发布说明文本。

    Args:
        changelog: 版本号到 ReleaseEntry 的映射字典。
        include_lib_changes: 是否包含以 "lib:" 开头的库变更条目。

    Returns:
        格式化后的发布说明文本。
    """
    parts: list[str] = []
    for ver, entry in changelog.items():
        s = f"[bold]{ver}[/bold]"
        if entry.description:
            s += f": {entry.description}"
        if entry.entries:
            for it in entry.entries:
                if it.lower().startswith("lib:") and not include_lib_changes:
                    continue
                s += "\n[markdown.item.bullet]• [/]" + it
        parts.append(s + "\n")
    return "\n".join(parts).strip()


_changelog_path = Path(__file__).parent.parent / "CHANGELOG.md"
CHANGELOG = (
    parse_changelog(_changelog_path.read_text(encoding="utf-8"))
    if _changelog_path.exists()
    else {}
)
"""解析后的更新日志数据。"""