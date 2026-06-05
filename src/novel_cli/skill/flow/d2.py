"""D2 Flowchart 解析模块。

将 D2 格式的 flowchart 文本解析为 Flow 图结构。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from . import (
    Flow,
    FlowEdge,
    FlowNode,
    FlowNodeKind,
    FlowParseError,
    validate_flow,
)

# 节点 ID 正则
_NODE_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
# markdown 块标记正则
_BLOCK_TAG_RE = re.compile(r"^\|md$")
# D2 属性字段集合
_PROPERTY_SEGMENTS = {
    "shape",
    "style",
    "label",
    "link",
    "icon",
    "near",
    "width",
    "height",
    "direction",
    "grid-rows",
    "grid-columns",
    "grid-gap",
    "font-size",
    "font-family",
    "font-color",
    "stroke",
    "fill",
    "opacity",
    "padding",
    "border-radius",
    "shadow",
    "sketch",
    "animated",
    "multiple",
    "constraint",
    "tooltip",
}


@dataclass(frozen=True, slots=True)
class _NodeDef:
    """节点定义（解析中间结构）。

    Attributes:
        node: FlowNode 实例。
        explicit: 是否为显式定义。
    """

    node: FlowNode
    explicit: bool


def parse_d2_flowchart(text: str) -> Flow:
    """解析 D2 flowchart 文本为 Flow 图结构。

    Args:
        text: D2 flowchart 文本内容。

    Returns:
        解析后的 Flow 对象。

    Raises:
        FlowParseError: 当解析失败时抛出。
        FlowValidationError: 当图结构无效时抛出。
    """
    # 将 D2 markdown 块规范化为引号标签，使解析器可保持行导向
    text = _normalize_markdown_blocks(text)
    nodes: dict[str, _NodeDef] = {}
    outgoing: dict[str, list[FlowEdge]] = {}

    for line_no, statement in _iter_top_level_statements(text):
        if _has_unquoted_token(statement, "->"):
            _parse_edge_statement(statement, line_no, nodes, outgoing)
        else:
            _parse_node_statement(statement, line_no, nodes)

    flow_nodes = {node_id: node_def.node for node_id, node_def in nodes.items()}
    for node_id in flow_nodes:
        outgoing.setdefault(node_id, [])

    flow_nodes = _infer_decision_nodes(flow_nodes, outgoing)
    begin_id, end_id = validate_flow(flow_nodes, outgoing)
    return Flow(nodes=flow_nodes, outgoing=outgoing, begin_id=begin_id, end_id=end_id)


def _normalize_markdown_blocks(text: str) -> str:
    """规范化 D2 markdown 块。

    将 `: |md` 块转换为多行引号标签，使解析器可保持行导向。

    Args:
        text: D2 文本内容。

    Returns:
        规范化后的文本。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    out_lines: list[str] = []
    i = 0
    line_no = 1

    while i < len(lines):
        line = lines[i]
        prefix, suffix = _split_unquoted_once(line, ":")
        if suffix is None:
            out_lines.append(line)
            i += 1
            line_no += 1
            continue

        suffix_clean = _strip_unquoted_comment(suffix).strip()
        # 仅将 `: |md` 视为 markdown 块起始
        if not _BLOCK_TAG_RE.fullmatch(suffix_clean):
            out_lines.append(line)
            i += 1
            line_no += 1
            continue

        start_line = line_no
        block_lines: list[str] = []
        i += 1
        line_no += 1
        while i < len(lines):
            block_line = lines[i]
            if block_line.strip() == "|":
                break
            block_lines.append(block_line)
            i += 1
            line_no += 1
        if i >= len(lines):
            raise FlowParseError(_line_error(start_line, "Unclosed markdown block"))

        # 将块转换为多行引号标签
        dedented = _dedent_block(block_lines)
        if dedented:
            escaped = [_escape_quoted_line(line) for line in dedented]
            out_lines.append(f'{prefix}: "{escaped[0]}')
            for line in escaped[1:]:
                out_lines.append(line)
            out_lines[-1] = f'{out_lines[-1]}"'
            out_lines.extend(["", ""])
        else:
            out_lines.append(f'{prefix}: ""')
            out_lines.append("")

        i += 1
        line_no += 1

    return "\n".join(out_lines)


def _strip_unquoted_comment(text: str) -> str:
    """移除非引号内的注释。

    Args:
        text: 文本内容。

    Returns:
        移除注释后的文本。
    """
    in_single = False
    in_double = False
    escape = False
    for idx, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and (in_single or in_double):
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            return text[:idx]
    return text


def _dedent_block(lines: list[str]) -> list[str]:
    """去除块缩进。

    Args:
        lines: 行列表。

    Returns:
        去缩进后的行列表。
    """
    indent: int | None = None
    for line in lines:
        if not line.strip():
            continue
        stripped = line.lstrip(" \t")
        lead = len(line) - len(stripped)
        if indent is None or lead < indent:
            indent = lead
    if indent is None:
        return ["" for _ in lines]
    return [line[indent:] if len(line) >= indent else "" for line in lines]


def _escape_quoted_line(line: str) -> str:
    """转义引号标签行。

    Args:
        line: 行内容。

    Returns:
        转义后的行内容。
    """
    return line.replace("\\", "\\\\").replace('"', '\\"')


def _iter_top_level_statements(text: str) -> Iterable[tuple[int, str]]:
    """遍历顶层语句。

    Args:
        text: D2 文本内容。

    Yields:
        (行号, 语句) 元组。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    brace_depth = 0
    in_single = False
    in_double = False
    escape = False
    drop_line = False
    buf: list[str] = []
    line_no = 1
    stmt_line = 1
    i = 0

    while i < len(text):
        ch = text[i]
        next_ch = text[i + 1] if i + 1 < len(text) else ""

        if ch == "\\" and next_ch == "\n":
            i += 2
            line_no += 1
            continue

        if ch == "\n":
            # 在引号内保留换行（用于 markdown 块标签）
            if (in_single or in_double) and brace_depth == 0 and not drop_line:
                buf.append("\n")
                line_no += 1
                i += 1
                continue
            if brace_depth == 0 and not in_single and not in_double and not drop_line:
                statement = "".join(buf).strip()
                if statement:
                    yield stmt_line, statement
            buf = []
            drop_line = False
            stmt_line = line_no + 1
            line_no += 1
            i += 1
            continue

        if not in_single and not in_double:
            if ch == "#":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue
            if ch == "{":
                if brace_depth == 0:
                    statement = "".join(buf).strip()
                    if statement:
                        yield stmt_line, statement
                    drop_line = True
                    buf.clear()
                brace_depth += 1
                i += 1
                continue
            if ch == "}" and brace_depth > 0:
                brace_depth -= 1
                i += 1
                continue
            if ch == "}" and brace_depth == 0:
                raise FlowParseError(_line_error(line_no, "Unmatched '}'"))

        if ch == "'" and not in_double and not escape:
            in_single = not in_single
        elif ch == '"' and not in_single and not escape:
            in_double = not in_double

        if escape:
            escape = False
        elif ch == "\\" and (in_single or in_double):
            escape = True

        if brace_depth == 0 and not drop_line:
            buf.append(ch)

        i += 1

    if brace_depth != 0:
        raise FlowParseError(_line_error(line_no, "Unclosed '{' block"))
    if in_single or in_double:
        raise FlowParseError(_line_error(line_no, "Unclosed string"))

    statement = "".join(buf).strip()
    if statement:
        yield stmt_line, statement


def _has_unquoted_token(text: str, token: str) -> bool:
    """判断文本中是否包含非引号内的标记。

    Args:
        text: 文本内容。
        token: 目标标记。

    Returns:
        包含标记时返回 True，否则返回 False。
    """
    parts = _split_on_token(text, token)
    return len(parts) > 1


def _parse_edge_statement(
    statement: str,
    line_no: int,
    nodes: dict[str, _NodeDef],
    outgoing: dict[str, list[FlowEdge]],
) -> None:
    """解析边定义语句。

    Args:
        statement: 语句内容。
        line_no: 行号。
        nodes: 节点字典。
        outgoing: 出边字典。

    Raises:
        FlowParseError: 当解析失败时抛出。
    """
    parts = _split_on_token(statement, "->")
    if len(parts) < 2:
        raise FlowParseError(_line_error(line_no, "Expected edge arrow"))

    last_part = parts[-1]
    target_text, edge_label = _split_unquoted_once(last_part, ":")
    parts[-1] = target_text

    node_ids: list[str] = []
    for idx, part in enumerate(parts):
        node_id = _parse_node_id(part, line_no, allow_inline_label=(idx < len(parts) - 1))
        node_ids.append(node_id)

    if any(_is_property_path(node_id) for node_id in node_ids):
        return
    if len(node_ids) < 2:
        raise FlowParseError(_line_error(line_no, "Edge must have at least two nodes"))

    label = _parse_label(edge_label, line_no) if edge_label is not None else None
    for idx in range(len(node_ids) - 1):
        edge = FlowEdge(
            src=node_ids[idx],
            dst=node_ids[idx + 1],
            label=label if idx == len(node_ids) - 2 else None,
        )
        outgoing.setdefault(edge.src, []).append(edge)
        outgoing.setdefault(edge.dst, [])

    for node_id in node_ids:
        _add_node(nodes, node_id=node_id, label=None, explicit=False, line_no=line_no)


def _parse_node_statement(statement: str, line_no: int, nodes: dict[str, _NodeDef]) -> None:
    """解析节点定义语句。

    Args:
        statement: 语句内容。
        line_no: 行号。
        nodes: 节点字典。

    Raises:
        FlowParseError: 当解析失败时抛出。
    """
    node_text, label_text = _split_unquoted_once(statement, ":")
    if label_text is not None and _is_property_path(node_text):
        return
    node_id = _parse_node_id(node_text, line_no, allow_inline_label=False)
    label = None
    explicit = False
    if label_text is not None and not label_text.strip():
        return
    if label_text is not None:
        label = _parse_label(label_text, line_no)
        explicit = True
    _add_node(nodes, node_id=node_id, label=label, explicit=explicit, line_no=line_no)


def _parse_node_id(text: str, line_no: int, *, allow_inline_label: bool) -> str:
    """解析节点 ID。

    Args:
        text: 文本内容。
        line_no: 行号。
        allow_inline_label: 是否允许内联标签。

    Returns:
        节点 ID。

    Raises:
        FlowParseError: 当解析失败时抛出。
    """
    cleaned = text.strip()
    if allow_inline_label and ":" in cleaned:
        cleaned = _split_unquoted_once(cleaned, ":")[0].strip()
    if not cleaned:
        raise FlowParseError(_line_error(line_no, "Expected node id"))
    match = _NODE_ID_RE.fullmatch(cleaned)
    if not match:
        raise FlowParseError(_line_error(line_no, f'Invalid node id "{cleaned}"'))
    return match.group(0)


def _is_property_path(node_id: str) -> bool:
    """判断是否为属性路径。

    Args:
        node_id: 节点 ID。

    Returns:
        是属性路径时返回 True，否则返回 False。
    """
    if "." not in node_id:
        return False
    parts = [part for part in node_id.split(".") if part]
    for part in parts[1:]:
        if part in _PROPERTY_SEGMENTS or part.startswith("style"):
            return True
    return parts[-1] in _PROPERTY_SEGMENTS


def _parse_label(text: str, line_no: int) -> str:
    """解析标签。

    Args:
        text: 文本内容。
        line_no: 行号。

    Returns:
        标签内容。

    Raises:
        FlowParseError: 当解析失败时抛出。
    """
    label = text.strip()
    if not label:
        raise FlowParseError(_line_error(line_no, "Label cannot be empty"))
    if label[0] in {"'", '"'}:
        return _parse_quoted_label(label, line_no)
    return label


def _parse_quoted_label(text: str, line_no: int) -> str:
    """解析引号标签。

    Args:
        text: 文本内容。
        line_no: 行号。

    Returns:
        标签内容。

    Raises:
        FlowParseError: 当解析失败时抛出。
    """
    quote = text[0]
    buf: list[str] = []
    escape = False
    i = 1
    while i < len(text):
        ch = text[i]
        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if ch == quote:
            trailing = text[i + 1 :].strip()
            if trailing:
                raise FlowParseError(_line_error(line_no, "Unexpected trailing content"))
            return "".join(buf)
        buf.append(ch)
        i += 1
    raise FlowParseError(_line_error(line_no, "Unclosed quoted label"))


def _split_on_token(text: str, token: str) -> list[str]:
    """在标记处分割文本（忽略引号内标记）。

    Args:
        text: 文本内容。
        token: 分割标记。

    Returns:
        分割后的文本片段列表。

    Raises:
        FlowParseError: 当字符串未闭合时抛出。
    """
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    escape = False
    i = 0

    while i < len(text):
        if not in_single and not in_double and text.startswith(token, i):
            parts.append("".join(buf).strip())
            buf = []
            i += len(token)
            continue
        ch = text[i]
        if escape:
            escape = False
        elif ch == "\\" and (in_single or in_double):
            escape = True
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        buf.append(ch)
        i += 1

    if in_single or in_double:
        raise FlowParseError("Unclosed string in statement")
    parts.append("".join(buf).strip())
    return parts


def _split_unquoted_once(text: str, token: str) -> tuple[str, str | None]:
    """在标记处分割文本一次（忽略引号内标记）。

    Args:
        text: 文本内容。
        token: 分割标记。

    Returns:
        (前部分, 后部分) 元组，无标记时后部分为 None。
    """
    in_single = False
    in_double = False
    escape = False
    for idx, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and (in_single or in_double):
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == token and not in_single and not in_double:
            return text[:idx].strip(), text[idx + 1 :].strip()
    return text.strip(), None


def _add_node(
    nodes: dict[str, _NodeDef],
    *,
    node_id: str,
    label: str | None,
    explicit: bool,
    line_no: int,
) -> FlowNode:
    """添加节点到节点字典。

    Args:
        nodes: 节点字典。
        node_id: 节点 ID。
        label: 节点标签。
        explicit: 是否为显式定义。
        line_no: 行号。

    Returns:
        添加或更新后的 FlowNode。

    Raises:
        FlowParseError: 当节点定义冲突时抛出。
    """
    label = label if label is not None else node_id
    label_norm = label.strip().lower()
    if not label:
        raise FlowParseError(_line_error(line_no, "Node label cannot be empty"))

    kind: FlowNodeKind = "task"
    if label_norm == "begin":
        kind = "begin"
    elif label_norm == "end":
        kind = "end"

    node = FlowNode(id=node_id, label=label, kind=kind)
    existing = nodes.get(node_id)
    if existing is None:
        nodes[node_id] = _NodeDef(node=node, explicit=explicit)
        return node

    if existing.node == node:
        return existing.node

    if not explicit and existing.explicit:
        return existing.node

    if explicit and not existing.explicit:
        nodes[node_id] = _NodeDef(node=node, explicit=True)
        return node

    raise FlowParseError(_line_error(line_no, f'Conflicting definition for node "{node_id}"'))


def _infer_decision_nodes(
    nodes: dict[str, FlowNode],
    outgoing: dict[str, list[FlowEdge]],
) -> dict[str, FlowNode]:
    """推断 decision 类型节点。

    将多出边的 task 节点推断为 decision 类型。

    Args:
        nodes: 节点字典。
        outgoing: 出边字典。

    Returns:
        更新后的节点字典。
    """
    updated: dict[str, FlowNode] = {}
    for node_id, node in nodes.items():
        kind = node.kind
        if kind == "task" and len(outgoing.get(node_id, [])) > 1:
            kind = "decision"
        if kind != node.kind:
            updated[node_id] = FlowNode(id=node.id, label=node.label, kind=kind)
        else:
            updated[node_id] = node
    return updated


def _line_error(line_no: int, message: str) -> str:
    """生成行错误信息。

    Args:
        line_no: 行号。
        message: 错误描述。

    Returns:
        格式化的错误信息。
    """
    return f"Line {line_no}: {message}"