"""Mermaid Flowchart 解析模块。

将 Mermaid 格式的 flowchart 文本解析为 Flow 图结构。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import (
    Flow,
    FlowEdge,
    FlowNode,
    FlowNodeKind,
    FlowParseError,
    validate_flow,
)


@dataclass(frozen=True, slots=True)
class _NodeSpec:
    """节点规格（解析中间结构）。

    Attributes:
        node_id: 节点 ID。
        label: 节点标签（可选）。
    """

    node_id: str
    label: str | None


@dataclass(slots=True)
class _NodeDef:
    """节点定义（解析中间结构）。

    Attributes:
        node: FlowNode 实例。
        explicit: 是否为显式定义。
    """

    node: FlowNode
    explicit: bool


# 节点 ID 正则
_NODE_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
# flowchart 头部正则
_HEADER_RE = re.compile(r"^(flowchart|graph)\b", re.IGNORECASE)

# 节点形状括号映射
_SHAPES = {
    "[": "]",
    "(" : ")",
    "{": "}",
}
# 管道标签正则
_PIPE_LABEL_RE = re.compile(r"\|([^|]*)\|")
# 边标签正则
_EDGE_LABEL_RE = re.compile(r"--\s*([^>-][^>]*)\s*-->")
# 箭头正则
_ARROW_RE = re.compile(r"[-.=]+>")


def parse_mermaid_flowchart(text: str) -> Flow:
    """解析 Mermaid flowchart 文本为 Flow 图结构。

    Args:
        text: Mermaid flowchart 文本内容。

    Returns:
        解析后的 Flow 对象。

    Raises:
        FlowParseError: 当解析失败时抛出。
        FlowValidationError: 当图结构无效时抛出。
    """
    nodes: dict[str, _NodeDef] = {}
    outgoing: dict[str, list[FlowEdge]] = {}

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw_line).strip()
        if not line or line.startswith("%%"):
            continue
        if _HEADER_RE.match(line):
            continue
        if _is_style_line(line):
            continue
        line = _strip_style_tokens(line)

        edge = _try_parse_edge_line(line, line_no)
        if edge is not None:
            src_spec, label, dst_spec = edge
            src_node = _add_node(nodes, src_spec, line_no)
            dst_node = _add_node(nodes, dst_spec, line_no)
            flow_edge = FlowEdge(src=src_node.id, dst=dst_node.id, label=label)
            outgoing.setdefault(flow_edge.src, []).append(flow_edge)
            outgoing.setdefault(flow_edge.dst, [])
            continue

        node_spec = _try_parse_node_line(line, line_no)
        if node_spec is not None:
            _add_node(nodes, node_spec, line_no)

    flow_nodes = {node_id: node_def.node for node_id, node_def in nodes.items()}
    for node_id in flow_nodes:
        outgoing.setdefault(node_id, [])

    flow_nodes = _infer_decision_nodes(flow_nodes, outgoing)
    begin_id, end_id = validate_flow(flow_nodes, outgoing)
    return Flow(nodes=flow_nodes, outgoing=outgoing, begin_id=begin_id, end_id=end_id)


def _try_parse_edge_line(line: str, line_no: int) -> tuple[_NodeSpec, str | None, _NodeSpec] | None:
    """尝试解析边定义行。

    Args:
        line: 行内容。
        line_no: 行号。

    Returns:
        (源节点规格, 边标签, 目标节点规格) 元组，非边定义时返回 None。
    """
    try:
        src_spec, idx = _parse_node_token(line, 0, line_no)
    except FlowParseError:
        return None

    normalized, label = _normalize_edge_line(line)
    idx = _skip_ws(normalized, idx)
    if ">" not in normalized[idx:]:
        if "---" not in normalized[idx:]:
            return None
        normalized = normalized[:idx] + normalized[idx:].replace("---", "-->", 1)

    normalized = _ARROW_RE.sub("-->", normalized)
    arrow_idx = normalized.rfind(">")
    if arrow_idx == -1:
        return None

    dst_start = _skip_ws(normalized, arrow_idx + 1)
    try:
        dst_spec, _ = _parse_node_token(normalized, dst_start, line_no)
    except FlowParseError:
        return None

    return src_spec, label, dst_spec


def _parse_node_token(line: str, idx: int, line_no: int) -> tuple[_NodeSpec, int]:
    """解析节点标记。

    Args:
        line: 行内容。
        idx: 解析起始位置。
        line_no: 行号。

    Returns:
        (节点规格, 结束位置) 元组。

    Raises:
        FlowParseError: 当解析失败时抛出。
    """
    match = _NODE_ID_RE.match(line, idx)
    if not match:
        raise FlowParseError(_line_error(line_no, "Expected node id"))
    node_id = match.group(0)
    idx = match.end()

    if idx >= len(line) or line[idx] not in _SHAPES:
        return _NodeSpec(node_id=node_id, label=None), idx

    close_char = _SHAPES[line[idx]]
    idx += 1
    label, idx = _parse_label(line, idx, close_char, line_no)
    return _NodeSpec(node_id=node_id, label=label), idx


def _parse_label(line: str, idx: int, close_char: str, line_no: int) -> tuple[str, int]:
    """解析节点标签。

    Args:
        line: 行内容。
        idx: 解析起始位置。
        close_char: 标签闭合字符。
        line_no: 行号。

    Returns:
        (标签内容, 结束位置) 元组。

    Raises:
        FlowParseError: 当解析失败时抛出。
    """
    if idx >= len(line):
        raise FlowParseError(_line_error(line_no, "Expected node label"))
    if close_char == ")" and line[idx] == "[":
        label, idx = _parse_label(line, idx + 1, "]", line_no)
        while idx < len(line) and line[idx].isspace():
            idx += 1
        if idx >= len(line) or line[idx] != ")":
            raise FlowParseError(_line_error(line_no, "Unclosed node label"))
        return label, idx + 1
    if line[idx] == '"':
        idx += 1
        buf: list[str] = []
        while idx < len(line):
            ch = line[idx]
            if ch == '"':
                idx += 1
                while idx < len(line) and line[idx].isspace():
                    idx += 1
                if idx >= len(line) or line[idx] != close_char:
                    raise FlowParseError(_line_error(line_no, "Unclosed node label"))
                return "".join(buf), idx + 1
            if ch == "\\" and idx + 1 < len(line):
                buf.append(line[idx + 1])
                idx += 2
                continue
            buf.append(ch)
            idx += 1
        raise FlowParseError(_line_error(line_no, "Unclosed quoted label"))

    end = line.find(close_char, idx)
    if end == -1:
        raise FlowParseError(_line_error(line_no, "Unclosed node label"))
    label = line[idx:end].strip()
    if not label:
        raise FlowParseError(_line_error(line_no, "Node label cannot be empty"))
    return label, end + 1


def _skip_ws(line: str, idx: int) -> int:
    """跳过空白字符。

    Args:
        line: 行内容。
        idx: 起始位置。

    Returns:
        跳过空白后的位置。
    """
    while idx < len(line) and line[idx].isspace():
        idx += 1
    return idx


def _add_node(nodes: dict[str, _NodeDef], spec: _NodeSpec, line_no: int) -> FlowNode:
    """添加节点到节点字典。

    Args:
        nodes: 节点字典。
        spec: 节点规格。
        line_no: 行号。

    Returns:
        添加或更新后的 FlowNode。

    Raises:
        FlowParseError: 当节点定义冲突时抛出。
    """
    label = spec.label if spec.label is not None else spec.node_id
    label_norm = label.strip().lower()
    if not label:
        raise FlowParseError(_line_error(line_no, "Node label cannot be empty"))

    kind: FlowNodeKind = "task"
    if label_norm == "begin":
        kind = "begin"
    elif label_norm == "end":
        kind = "end"

    node = FlowNode(id=spec.node_id, label=label, kind=kind)
    explicit = spec.label is not None

    existing = nodes.get(spec.node_id)
    if existing is None:
        nodes[spec.node_id] = _NodeDef(node=node, explicit=explicit)
        return node

    if existing.node == node:
        return existing.node

    if not explicit and existing.explicit:
        return existing.node

    if explicit and not existing.explicit:
        nodes[spec.node_id] = _NodeDef(node=node, explicit=True)
        return node

    raise FlowParseError(_line_error(line_no, f'Conflicting definition for node "{spec.node_id}"'))


def _line_error(line_no: int, message: str) -> str:
    """生成行错误信息。

    Args:
        line_no: 行号。
        message: 错误描述。

    Returns:
        格式化的错误信息。
    """
    return f"Line {line_no}: {message}"


def _strip_comment(line: str) -> str:
    """移除行内注释。

    Args:
        line: 行内容。

    Returns:
        移除注释后的行内容。
    """
    if "%%" not in line:
        return line
    return line.split("%%", 1)[0]


def _is_style_line(line: str) -> bool:
    """判断是否为样式定义行。

    Args:
        line: 行内容。

    Returns:
        是样式行时返回 True，否则返回 False。
    """
    lowered = line.lower()
    if lowered in ("end",):
        return True
    return lowered.startswith(
        (
            "classdef ",
            "class ",
            "style ",
            "linkstyle ",
            "click ",
            "subgraph ",
            "direction ",
        )
    )


def _strip_style_tokens(line: str) -> str:
    """移除样式标记。

    Args:
        line: 行内容。

    Returns:
        移除样式标记后的行内容。
    """
    return re.sub(r":::[A-Za-z0-9_-]+", "", line)


def _try_parse_node_line(line: str, line_no: int) -> _NodeSpec | None:
    """尝试解析节点定义行。

    Args:
        line: 行内容。
        line_no: 行号。

    Returns:
        节点规格，非节点定义时返回 None。
    """
    try:
        node_spec, _ = _parse_node_token(line, 0, line_no)
    except FlowParseError:
        return None
    return node_spec


def _normalize_edge_line(line: str) -> tuple[str, str | None]:
    """规范化边定义行。

    Args:
        line: 行内容。

    Returns:
        (规范化后的行, 边标签) 元组。
    """
    label = None
    normalized = line
    pipe_match = _PIPE_LABEL_RE.search(normalized)
    if pipe_match:
        label = pipe_match.group(1).strip() or None
        normalized = normalized[: pipe_match.start()] + normalized[pipe_match.end() :]
    if label is None:
        edge_match = _EDGE_LABEL_RE.search(normalized)
        if edge_match:
            label = edge_match.group(1).strip() or None
            normalized = normalized[: edge_match.start()] + "-->" + normalized[edge_match.end() :]
    return normalized, label


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