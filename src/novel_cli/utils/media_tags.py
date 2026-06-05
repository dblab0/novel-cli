"""媒体标签处理工具。

本模块提供将媒体内容部分包装为带标签的内容块的功能，
用于在消息中标识和格式化媒体内容。
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape

from novel_cli.wire.types import ContentPart, TextPart


def _format_tag(tag: str, attrs: Mapping[str, str | None] | None = None) -> str:
    """格式化 HTML 标签字符串。

    Args:
        tag: 标签名称。
        attrs: 标签属性字典，值可以为 None（将被忽略）。

    Returns:
        格式化后的 HTML 标签字符串。
    """
    if not attrs:
        return f"<{tag}>"
    rendered: list[str] = []
    for key, value in sorted(attrs.items()):
        if not value:
            continue
        rendered.append(f'{key}="{escape(str(value), quote=True)}"')
    if not rendered:
        return f"<{tag}>"
    return f"<{tag} " + " ".join(rendered) + ">"


def wrap_media_part(
    part: ContentPart, *, tag: str, attrs: Mapping[str, str | None] | None = None
) -> list[ContentPart]:
    """将媒体内容部分包装为带标签的内容块。

    Args:
        part: 要包装的媒体内容部分。
        tag: 包装标签名称。
        attrs: 标签属性字典。

    Returns:
        包含开始标签、内容和结束标签的内容部分列表。
    """
    return [
        TextPart(text=_format_tag(tag, attrs)),
        part,
        TextPart(text=f"</{tag}>"),
    ]