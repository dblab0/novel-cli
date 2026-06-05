"""YAML 前言解析工具。

本模块提供从文本或文件中解析 YAML 前言（frontmatter）的功能，
常用于解析 Markdown 文件中的元数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """从文本中解析 YAML 前言。

    Args:
        text: 可能包含 YAML 前言的文本内容。

    Returns:
        解析后的前言字典，如果没有前言则返回 None。

    Raises:
        ValueError: 前言 YAML 格式无效或不是映射类型时抛出。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    frontmatter_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        frontmatter_lines.append(line)
    else:
        return None

    frontmatter = "\n".join(frontmatter_lines).strip()
    if not frontmatter:
        return None

    try:
        raw_data: Any = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid frontmatter YAML.") from exc

    if not isinstance(raw_data, dict):
        raise ValueError("Frontmatter YAML must be a mapping.")

    return cast(dict[str, Any], raw_data)


def read_frontmatter(path: Path) -> dict[str, Any] | None:
    """读取文件开头的 YAML 前言。

    Args:
        path: 可能包含前言的文件路径。

    Returns:
        解析后的前言字典，如果没有前言则返回 None。
    """
    return parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))