"""CLI 工具结果和审批面板的统一 diff 渲染模块。

所有 diff 渲染均通过此模块处理：
- ``render_diff_panel``  — 带 Panel、Table、背景色的完整 diff（工具结果和分页器）
- ``render_diff_preview`` — 紧凑的变更行预览（审批面板）
- ``collect_diff_hunks``  — 从 DiffDisplayBlocks 准备共享数据
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum, auto

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from novel_cli.tools.display import DiffDisplayBlock
from novel_cli.ui.theme import get_diff_colors
from novel_cli.utils.rich.syntax import NovelSyntax

_INLINE_DIFF_MIN_RATIO = 0.5  # 当行差异过大时跳过行内 diff

MAX_PREVIEW_CHANGED_LINES = 6


# ---------------------------------------------------------------------------
# 数据模型 — 解析后的 diff 行
# ---------------------------------------------------------------------------


class DiffLineKind(Enum):
    """diff 行类型枚举。

    Attributes:
        CONTEXT: 上下文行（无变更）。
        ADD: 新增行。
        DELETE: 删除行。
    """

    CONTEXT = auto()
    ADD = auto()
    DELETE = auto()


@dataclass(slots=True)
class DiffLine:
    """单行 diff 数据结构。

    Attributes:
        kind: 行类型（上下文/新增/删除）。
        old_num: 原文件行号，0 表示不适用（如新增行无旧行号）。
        new_num: 新文件行号，0 表示不适用（如删除行无新行号）。
        code: 原始代码文本。
        content: 高亮后的 Rich Text 内容，语法高亮后填充。
        is_inline_paired: 是否已配对用于行内 diff 对比。
    """

    kind: DiffLineKind
    old_num: int  # 0 表示不适用（如新增行无旧行号）
    new_num: int  # 0 表示不适用（如删除行无新行号）
    code: str
    content: Text | None = None  # 语法高亮后填充
    is_inline_paired: bool = False  # 是否已配对用于行内 diff 对比


# ---------------------------------------------------------------------------
# 核心：通过 SequenceMatcher 从 old_text/new_text 直接构建 DiffLines
# ---------------------------------------------------------------------------


def _build_diff_lines(
    old_text: str,
    new_text: str,
    old_start: int,
    new_start: int,
    n_context: int = 3,
) -> list[list[DiffLine]]:
    """从旧/新文本直接构建分组的 DiffLine hunks。

    Args:
        old_text: 原始文件文本内容。
        new_text: 新文件文本内容。
        old_start: 原文件起始行号。
        new_start: 新文件起始行号。
        n_context: 上下文行数，默认为 3。

    Returns:
        hunks 列表，每个 hunk 是 DiffLine 对象列表。
        此方法替代 format_unified_diff → parse 的往返流程。
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)

    hunks: list[list[DiffLine]] = []
    for group in matcher.get_grouped_opcodes(n=n_context):
        hunk: list[DiffLine] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for k in range(i2 - i1):
                    hunk.append(
                        DiffLine(
                            kind=DiffLineKind.CONTEXT,
                            old_num=old_start + i1 + k,
                            new_num=new_start + j1 + k,
                            code=old_lines[i1 + k],
                        )
                    )
            elif tag == "delete":
                for k in range(i2 - i1):
                    hunk.append(
                        DiffLine(
                            kind=DiffLineKind.DELETE,
                            old_num=old_start + i1 + k,
                            new_num=0,
                            code=old_lines[i1 + k],
                        )
                    )
            elif tag == "insert":
                for k in range(j2 - j1):
                    hunk.append(
                        DiffLine(
                            kind=DiffLineKind.ADD,
                            old_num=0,
                            new_num=new_start + j1 + k,
                            code=new_lines[j1 + k],
                        )
                    )
            elif tag == "replace":
                for k in range(i2 - i1):
                    hunk.append(
                        DiffLine(
                            kind=DiffLineKind.DELETE,
                            old_num=old_start + i1 + k,
                            new_num=0,
                            code=old_lines[i1 + k],
                        )
                    )
                for k in range(j2 - j1):
                    hunk.append(
                        DiffLine(
                            kind=DiffLineKind.ADD,
                            old_num=0,
                            new_num=new_start + j1 + k,
                            code=new_lines[j1 + k],
                        )
                    )
        if hunk:
            hunks.append(hunk)
    return hunks


# ---------------------------------------------------------------------------
# 语法高亮与行内 diff
# ---------------------------------------------------------------------------


def _make_highlighter(path: str) -> NovelSyntax:
    """根据文件扩展名创建语法高亮器。

    Args:
        path: 文件路径，用于提取扩展名。

    Returns:
        配置好语言的 NovelSyntax 实例。
    """
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return NovelSyntax("", ext if ext else "text")


def _highlight(highlighter: NovelSyntax, code: str) -> Text:
    """对单行代码进行语法高亮。

    Args:
        highlighter: 语法高亮器实例。
        code: 待高亮的代码文本。

    Returns:
        高亮后的 Rich Text 对象。
    """
    t = highlighter.highlight(code)
    t.rstrip()
    return t


def _apply_inline_diff(
    highlighter: NovelSyntax,
    del_lines: list[DiffLine],
    add_lines: list[DiffLine],
) -> None:
    """配对删除/新增行并应用词级别的行内 diff 高亮。

    就地修改配对行的 DiffLine.content。

    Args:
        highlighter: 语法高亮器实例。
        del_lines: 删除行列表。
        add_lines: 新增行列表。
    """
    colors = get_diff_colors()
    paired = min(len(del_lines), len(add_lines))
    for j in range(paired):
        old_code = del_lines[j].code
        new_code = add_lines[j].code
        sm = SequenceMatcher(None, old_code, new_code)
        if sm.ratio() < _INLINE_DIFF_MIN_RATIO:
            continue
        old_text = _highlight(highlighter, old_code)
        new_text = _highlight(highlighter, new_code)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op in ("delete", "replace"):
                old_text.stylize(colors.del_hl, i1, i2)
            if op in ("insert", "replace"):
                new_text.stylize(colors.add_hl, j1, j2)
        del_lines[j].content = old_text
        del_lines[j].is_inline_paired = True
        add_lines[j].content = new_text
        add_lines[j].is_inline_paired = True


def _highlight_hunk(highlighter: NovelSyntax, hunk: list[DiffLine]) -> None:
    """高亮 hunk 中的所有行，并对配对的 -/+ 块应用行内 diff。

    Args:
        highlighter: 语法高亮器实例。
        hunk: 待处理的 DiffLine 列表。
    """
    # 第一遍：查找连续的 -/+ 块并应用行内 diff
    i = 0
    while i < len(hunk):
        if hunk[i].kind == DiffLineKind.DELETE:
            del_start = i
            while i < len(hunk) and hunk[i].kind == DiffLineKind.DELETE:
                i += 1
            add_start = i
            while i < len(hunk) and hunk[i].kind == DiffLineKind.ADD:
                i += 1
            _apply_inline_diff(
                highlighter,
                hunk[del_start:add_start],
                hunk[add_start:i],
            )
        else:
            i += 1

    # 第二遍：高亮尚未被行内 diff 处理的行
    for dl in hunk:
        if dl.content is None:
            dl.content = _highlight(highlighter, dl.code)


# ---------------------------------------------------------------------------
# 共享标题构建器
# ---------------------------------------------------------------------------


def _build_diff_header(path: str, added: int, removed: int) -> Text:
    """构建文件标题文本：统计信息 + 路径。

    Args:
        path: 文件路径。
        added: 新增行数。
        removed: 删除行数。

    Returns:
        包含统计和路径的 Rich Text 对象。
    """
    header = Text()
    if added > 0:
        header.append(f"+{added} ", style="bold green")
    if removed > 0:
        header.append(f"-{removed} ", style="bold red")
    header.append(path)
    return header


# ---------------------------------------------------------------------------
# 公开接口：从 DiffDisplayBlocks 收集 hunks
# ---------------------------------------------------------------------------


def collect_diff_hunks(
    blocks: list[DiffDisplayBlock],
) -> tuple[list[list[DiffLine]], int, int]:
    """从同一文件的 DiffDisplayBlock 列表构建解析后的 DiffLine hunks 和统计信息。

    Args:
        blocks: 同一文件的 DiffDisplayBlock 列表。

    Returns:
        元组 (hunks, added_total, removed_total)，其中每个 hunk 是 DiffLine 列表。
    """
    all_hunks: list[list[DiffLine]] = []
    added = 0
    removed = 0
    for b in blocks:
        block_hunks = _build_diff_lines(
            b.old_text,
            b.new_text,
            b.old_start,
            b.new_start,
        )
        for hunk in block_hunks:
            for dl in hunk:
                if dl.kind == DiffLineKind.ADD:
                    added += 1
                elif dl.kind == DiffLineKind.DELETE:
                    removed += 1
            all_hunks.append(hunk)
    return all_hunks, added, removed


# ---------------------------------------------------------------------------
# 公开接口：完整 diff 面板（工具结果和分页器）
# ---------------------------------------------------------------------------


def render_diff_panel(
    path: str,
    hunks: list[list[DiffLine]],
    added: int,
    removed: int,
) -> RenderableType:
    """渲染带边框 Panel 的 diff，包含行号、背景色、语法高亮和行内变更标记。

    Args:
        path: 文件路径。
        hunks: DiffLine hunk 列表。
        added: 新增行数。
        removed: 删除行数。

    Returns:
        可渲染的 Panel 对象。
    """
    title = Text()
    title.append(" ")
    title.append_text(_build_diff_header(path, added, removed))
    title.append(" ")

    highlighter = _make_highlighter(path)
    for hunk in hunks:
        _highlight_hunk(highlighter, hunk)

    # 计算行号列宽度
    max_ln = 0
    for hunk in hunks:
        for dl in hunk:
            max_ln = max(max_ln, dl.old_num, dl.new_num)
    num_width = max(len(str(max_ln)), 2)

    table = Table(
        show_header=False,
        box=None,
        padding=(0, 0),
        show_edge=False,
        expand=True,
    )
    table.add_column(justify="right", width=num_width, no_wrap=True)
    table.add_column(width=3, no_wrap=True)
    table.add_column(ratio=1)

    colors = get_diff_colors()
    for hunk_idx, hunk in enumerate(hunks):
        if hunk_idx > 0:
            table.add_row(Text("⋮", style="dim"), Text(""), Text(""))

        for dl in hunk:
            assert dl.content is not None
            if dl.kind == DiffLineKind.ADD:
                table.add_row(
                    Text(str(dl.new_num)),
                    Text(" + ", style="green"),
                    dl.content,
                    style=colors.add_bg,
                )
            elif dl.kind == DiffLineKind.DELETE:
                table.add_row(
                    Text(str(dl.old_num)),
                    Text(" - ", style="red"),
                    dl.content,
                    style=colors.del_bg,
                )
            else:
                table.add_row(
                    Text(str(dl.new_num), style="dim"),
                    Text("   "),
                    dl.content,
                )

    return Panel(
        table,
        title=title,
        title_align="left",
        border_style="dim",
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# 公开接口：紧凑预览（审批面板）
# ---------------------------------------------------------------------------


def render_diff_preview(
    path: str,
    hunks: list[list[DiffLine]],
    added: int,
    removed: int,
    max_lines: int = MAX_PREVIEW_CHANGED_LINES,
) -> tuple[list[RenderableType], int]:
    """渲染紧凑的 diff 预览，仅显示变更行（无上下文）。

    Args:
        path: 文件路径。
        hunks: DiffLine hunk 列表。
        added: 新增行数。
        removed: 删除行数。
        max_lines: 最大显示行数，默认为 MAX_PREVIEW_CHANGED_LINES。

    Returns:
        元组 (renderables, remaining_count)：Rich 可渲染对象列表和
        未显示的变更行数。
    """
    highlighter = _make_highlighter(path)
    for hunk in hunks:
        _highlight_hunk(highlighter, hunk)

    # 仅收集所有 hunk 中的变更行
    changed: list[DiffLine] = []
    for hunk in hunks:
        for dl in hunk:
            if dl.kind != DiffLineKind.CONTEXT:
                changed.append(dl)

    total = len(changed)
    shown = changed[:max_lines]
    remaining = total - len(shown)

    # 根据显示的行计算行号列宽度
    max_ln = max(
        (dl.old_num if dl.kind == DiffLineKind.DELETE else dl.new_num for dl in shown),
        default=0,
    )
    num_width = max(len(str(max_ln)), 2)

    result: list[RenderableType] = [_build_diff_header(path, added, removed)]

    for dl in shown:
        assert dl.content is not None
        line = Text()
        ln = dl.old_num if dl.kind == DiffLineKind.DELETE else dl.new_num
        line.append(str(ln).rjust(num_width), style="dim")
        marker_style = "green" if dl.kind == DiffLineKind.ADD else "red"
        marker_char = "+" if dl.kind == DiffLineKind.ADD else "-"
        line.append(f" {marker_char} ", style=marker_style)
        line.append_text(dl.content)
        result.append(line)

    if remaining > 0:
        result.append(Text(f"... {remaining} more lines (ctrl-e to expand)", style="dim italic"))

    return result, remaining


# ---------------------------------------------------------------------------
# 公开接口：大文件摘要渲染器
# ---------------------------------------------------------------------------


def _summary_description(blocks: list[DiffDisplayBlock]) -> str:
    """从摘要块构建人类可读的大小描述。

    Args:
        blocks: DiffDisplayBlock 列表。

    Returns:
        文件大小描述字符串。
    """
    block = blocks[0]
    if block.old_text == "(0 lines)":
        return f"New file with {block.new_text.strip('()')}"
    if block.old_text == block.new_text:
        return block.old_text.strip("()")
    return f"{block.old_text.strip('()')} \u2192 {block.new_text.strip('()')}"


def render_diff_summary_panel(
    path: str,
    blocks: list[DiffDisplayBlock],
) -> RenderableType:
    """为过大的文件渲染摘要面板。

    Args:
        path: 文件路径。
        blocks: DiffDisplayBlock 列表。

    Returns:
        可渲染的 Panel 对象。
    """
    title = Text()
    title.append(" ")
    title.append(path)
    title.append(" ")

    body = Text()
    body.append("File too large for inline diff", style="dim italic")
    body.append("\n")
    body.append(_summary_description(blocks), style="dim")

    return Panel(
        body,
        title=title,
        title_align="left",
        border_style="dim",
        padding=(1, 2),
    )


def render_diff_summary_preview(
    path: str,
    blocks: list[DiffDisplayBlock],
) -> list[RenderableType]:
    """为审批面板渲染紧凑的摘要预览。

    Args:
        path: 文件路径。
        blocks: DiffDisplayBlock 列表。

    Returns:
        可渲染对象列表。
    """
    header = Text()
    header.append(path)
    desc = Text()
    summary = _summary_description(blocks)
    desc.append(f"  File too large for inline diff ({summary})", style="dim italic")
    return [header, desc]
