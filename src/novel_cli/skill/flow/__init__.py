"""Flow 图结构定义与解析模块。

定义 Flow 图的核心数据结构（节点、边、图），并提供解析错误类型和验证函数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from kosong.message import ContentPart

# Flow 节点类型枚举
FlowNodeKind = Literal["begin", "end", "task", "decision"]


class FlowError(ValueError):
    """Flow 相关错误基类。"""


class FlowParseError(FlowError):
    """Flow 解析错误。

    当 prompt flow 解析失败时抛出。
    """


class FlowValidationError(FlowError):
    """Flow 验证错误。

    当 flowchart 校验失败时抛出。
    """


@dataclass(frozen=True, slots=True)
class FlowNode:
    """Flow 图节点。

    Attributes:
        id: 节点唯一标识符。
        label: 节点标签（字符串或 ContentPart 列表）。
        kind: 节点类型（begin/end/task/decision）。
    """

    id: str
    label: str | list[ContentPart]
    kind: FlowNodeKind


@dataclass(frozen=True, slots=True)
class FlowEdge:
    """Flow 图边。

    Attributes:
        src: 源节点 ID。
        dst: 目标节点 ID。
        label: 边标签（可选）。
    """

    src: str
    dst: str
    label: str | None


@dataclass(slots=True)
class Flow:
    """Flow 图结构。

    Attributes:
        nodes: 节点字典（节点 ID -> FlowNode）。
        outgoing: 出边字典（节点 ID -> 边列表）。
        begin_id: 起始节点 ID。
        end_id: 结束节点 ID。
    """

    nodes: dict[str, FlowNode]
    outgoing: dict[str, list[FlowEdge]]
    begin_id: str
    end_id: str


# choice 标签解析正则
_CHOICE_RE = re.compile(r"<choice>([^<]*)</choice>")


def parse_choice(text: str) -> str | None:
    """解析 choice 标签内容。

    从文本中提取最后一个 <choice> 标签的内容。

    Args:
        text: 包含 choice 标签的文本。

    Returns:
        choice 内容字符串，无匹配时返回 None。
    """
    matches = _CHOICE_RE.findall(text or "")
    if not matches:
        return None
    return matches[-1].strip()


def validate_flow(
    nodes: dict[str, FlowNode],
    outgoing: dict[str, list[FlowEdge]],
) -> tuple[str, str]:
    """验证 Flow 图结构。

    确保图中只有一个 BEGIN 和一个 END 节点，END 节点可从 BEGIN 达到，
    且多出边节点的边标签不重复且非空。

    Args:
        nodes: 节点字典。
        outgoing: 出边字典。

    Returns:
        (begin_id, end_id) 元组。

    Raises:
        FlowValidationError: 当图结构无效时抛出。
    """
    begin_ids = [node.id for node in nodes.values() if node.kind == "begin"]
    end_ids = [node.id for node in nodes.values() if node.kind == "end"]

    if len(begin_ids) != 1:
        raise FlowValidationError(f"Expected exactly one BEGIN node, found {len(begin_ids)}")
    if len(end_ids) != 1:
        raise FlowValidationError(f"Expected exactly one END node, found {len(end_ids)}")

    begin_id = begin_ids[0]
    end_id = end_ids[0]

    # 计算从 BEGIN 可达的节点集合
    reachable: set[str] = set()
    queue: list[str] = [begin_id]
    while queue:
        node_id = queue.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        for edge in outgoing.get(node_id, []):
            if edge.dst not in reachable:
                queue.append(edge.dst)

    # 检查多出边节点的边标签有效性
    for node in nodes.values():
        if node.id not in reachable:
            continue
        edges = outgoing.get(node.id, [])
        if len(edges) <= 1:
            continue
        labels: list[str] = []
        for edge in edges:
            if edge.label is None or not edge.label.strip():
                raise FlowValidationError(f'Node "{node.id}" has an unlabeled edge')
            labels.append(edge.label)
        if len(set(labels)) != len(labels):
            raise FlowValidationError(f'Node "{node.id}" has duplicate edge labels')

    if end_id not in reachable:
        raise FlowValidationError("END node is not reachable from BEGIN")

    return begin_id, end_id