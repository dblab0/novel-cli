"""小说搜索工具结果格式化器。

提供各种格式化函数，用于将实体搜索、关系图查询、关键词搜索和章节读取的结果
转换为可读的文本输出。
"""

from __future__ import annotations

import re

from kosong.tooling import ToolError, ToolOk, ToolReturnValue

from novel_cli.store.models import CorpusResult, Entity, GraphResult

MAX_CONTENT_LENGTH = 20000


def format_entity_result(entities: list[Entity]) -> ToolReturnValue:
    """格式化实体搜索结果。

    Args:
        entities: 实体列表。

    Returns:
        格式化后的工具返回值，包含实体信息的文本输出。
    """
    if not entities:
        return ToolOk(
            output="未找到匹配的实体。",
            message=(
                "未找到匹配实体。\n"
                "如果是 name 模式 → 必须切换为 vector 模式: SearchEntity(query='原关键词', search_mode='vector')\n"
                "如果 vector 也无结果 → 直接用 SearchCorpus(keyword='关键词') 搜索原文，跳过 Graph 步骤。"
            ),
            brief="未找到实体",
        )

    lines: list[str] = [f"找到 {len(entities)} 个实体："]
    for i, e in enumerate(entities, 1):
        lines.append(f"\n[{i}] {e.id}")
        alias_part = f" | 别名: {e.alias}" if e.alias else ""
        lines.append(f"    名称: {e.name}{alias_part} | 类型: {e.type} | 书籍: {e.book}")
        if len(e.description) > 200:
            desc = e.description[:200] + f"...(共 {len(e.description)} 字，已截断)"
        else:
            desc = e.description
        lines.append(f"    描述: {desc}")
        if e.score is not None:
            lines.append(f"    匹配度: {e.score:.4f}")

    first = entities[0]
    first_id = first.id

    # 构建别名关键词提示
    alias_hint = ""
    entities_with_alias = [e for e in entities if e.alias]
    if entities_with_alias:
        alias_keywords = ", ".join(
            f"'{e.alias}'" for e in entities_with_alias
        )
        alias_hint = (
            f"\n别名关键词提示: 这些实体有别名 [{alias_keywords}]，"
            "后续 SearchCorpus 时若主名称无结果或内容不足，"
            "可用别名作为替代关键词重试。"
        )

    nav_message = (
        f"查询实体详情和关系类型: SearchGraph(entity_id='{first_id}')"
        f"{alias_hint}"
    )
    return ToolOk(
        output="\n".join(lines),
        message=nav_message,
        brief=f"找到 {len(entities)} 个实体",
    )


def format_graph_overview(
    entity_id: str,
    desc_result: GraphResult,
    types_result: GraphResult,
) -> ToolReturnValue:
    """格式化 Graph 概览（描述 + 关系类型）。

    Args:
        entity_id: 实体 ID。
        desc_result: 描述查询结果。
        types_result: 关系类型查询结果。

    Returns:
        格式化后的工具返回值。
    """
    lines: list[str] = [f"实体：{entity_id}"]

    # 别名部分
    if desc_result.alias:
        lines.append(f"别名：{desc_result.alias}")

    # 描述部分
    if desc_result.description:
        lines.append(f"\n【描述】\n{desc_result.description}")

    # 关系类型部分
    if not types_result.types:
        lines.append("\n【关系类型】无")
        nav_message = "该实体没有关系，可能 entity_id 有误"
    else:
        lines.append(f"\n【关系类型】共 {len(types_result.types)} 种")
        for rt in types_result.types:
            lines.append(f"  【{rt.category}】{rt.type} — {rt.description}")

        # 在 message 中列出实际可用的 TYPE_NAME
        type_names = [rt.type for rt in types_result.types[:10]]
        names_str = ", ".join(f"'{t}'" for t in type_names)
        suffix = ", ..." if len(types_result.types) > 10 else ""
        nav_message = (
            f"已列出 {len(types_result.types)} 种关系类型。可用的 TYPE_NAME: [{names_str}{suffix}]\n"
            f"下一步: 选择与问题相关的 TYPE_NAME，调用\n"
            f"SearchGraph(entity_id='{entity_id}', rel_type='从上方 TYPE_NAME 中选取')"
        )

    return ToolOk(
        output="\n".join(lines),
        message=nav_message,
        brief=f"描述 + {len(types_result.types)} 种关系类型" if types_result.types else "无关系类型",
    )


def format_graph_rels(
    entity_id: str,
    rel_types: list[str],
    data: GraphResult,
) -> ToolReturnValue:
    """格式化指定关系类型的关系详情。

    Args:
        entity_id: 实体 ID。
        rel_types: 关系类型名称列表。
        data: 关系图数据。

    Returns:
        格式化后的工具返回值，包含关系详情列表。
    """
    if not data.rels:
        if len(rel_types) == 1:
            return ToolOk(
                output=f"实体「{entity_id}」没有「{rel_types[0]}」类型的关系。",
                message=f"未找到「{rel_types[0]}」类型的关系，请确认类型名称是否正确",
                brief="无关系详情",
            )
        type_names = "、".join(f"「{t}」" for t in rel_types)
        return ToolOk(
            output=f"实体「{entity_id}」没有 {type_names} 类型的关系。",
            message=f"未找到 {type_names} 类型的关系，请确认类型名称是否正确",
            brief="无关系详情",
        )

    lines: list[str] = []
    desc_texts: list[str] = []
    is_multi = len(rel_types) > 1

    if is_multi:
        # 多类型：按类型分组，带【】标题
        global_idx = 0
        for rel_type in rel_types:
            matched = [r for r in data.rels if r.relationship_type == rel_type]
            if not matched:
                lines.append(f"【{rel_type}】无数据\n")
                continue
            lines.append(f"【{rel_type}】")
            for rel in matched:
                global_idx += 1
                lines.append(f"[{global_idx}] -> {rel.other_node_id}")
                if rel.description:
                    lines.append(f"    描述: {rel.description}")
                    desc_texts.append(rel.description)
                lines.append("")
            lines.append("")
    else:
        # 单类型：保持原格式，不加分组标题
        rel_type = rel_types[0]
        lines.append(f"实体「{entity_id}」的「{rel_type}」关系：\n")
        for i, rel in enumerate(data.rels, 1):
            lines.append(f"[{i}] -> {rel.other_node_id}")
            if rel.description:
                lines.append(f"    描述: {rel.description}")
                desc_texts.append(rel.description)
            lines.append("")

    # 溢出保护：超过上限直接报错
    output = "\n".join(lines)
    if len(output) > MAX_CONTENT_LENGTH:
        return ToolError(
            message=f"查询结果过大（{len(output)} 字符，上限 {MAX_CONTENT_LENGTH}）。请减少关系类型数量，分批查询。",
            brief="结果过大，请减少关系类型",
        )

    # 提取并展示 chapter_ids
    chapter_ids = _extract_chapter_ids(*desc_texts)
    lines.extend(_format_chapter_ids_hint(chapter_ids))

    # 引导使用 chapter_ids 进 SearchCorpus 或 ReadChapter
    if chapter_ids:
        ids_str = ", ".join(str(i) for i in chapter_ids[:20])
        nav_message = (
            f"已返回关系详情。从描述中提取到 chapter_ids: [{ids_str}]。\n"
            f"下一步: SearchCorpus(keyword='核心关键词', chapter_ids=[{ids_str}])\n"
            "keyword 用 1 个最核心的名词。\n"
            "如果上方关系详情已包含完整答案，可直接回答，无需调用 SearchCorpus。"
        )
    else:
        nav_message = (
            "已返回关系详情，但未提取到 chapter_ids。\n"
            "如需原文: SearchCorpus(keyword='关键词') 全书搜索。\n"
            "如果关系详情已足够回答问题，可直接回答。"
        )

    output = "\n".join(lines)
    return ToolOk(
        output=output,
        message=nav_message,
        brief=f"{len(data.rels)} 条关系详情",
    )


def _extract_chapter_ids(*texts: str) -> list[int]:
    """从 description 文本中提取章节 ID。

    graph 和 entity 的 description 格式为 [{'437': '描述文字'}, {'441': '描述文字'}]，
    其中字典 key 是 chapter_id / document_id。

    Args:
        texts: 待提取的描述文本列表。

    Returns:
        去重排序后的 chapter_id 列表。
    """
    ids: set[int] = set()
    for text in texts:
        # 匹配 {'数字': 或 {'数字', 格式
        for m in re.finditer(r"\{'(\d+)'", text):
            ids.add(int(m.group(1)))
    return sorted(ids)


def _format_chapter_ids_hint(chapter_ids: list[int]) -> list[str]:
    """生成 chapter_ids 使用提示行。

    Args:
        chapter_ids: 章节 ID 列表。

    Returns:
        提示行列表，为空时返回空列表。
    """
    if not chapter_ids:
        return []
    return ["\n→ 可使用 SearchCorpus(chapter_ids=[章节号列表], keyword='核心词') 缩小搜索范围"]


def format_corpus_search_result(book: str, data: CorpusResult) -> ToolReturnValue:
    """格式化关键词搜索结果。

    Args:
        book: 书名。
        data: 搜索结果数据。

    Returns:
        格式化后的工具返回值，包含搜索结果文本。
    """
    output = data.to_text()

    # 溢出保护：超过上限直接报错
    if len(output) > MAX_CONTENT_LENGTH:
        return ToolError(
            message=f"查询结果过大（{len(output)} 字符，上限 {MAX_CONTENT_LENGTH}）。请减少 chapter_ids 数量，分批查询。",
            brief="结果过大，请减少 chapter_ids",
        )

    # 空结果时的降级引导
    if not output:
        return ToolOk(
            output=output,
            message=(
                "搜索返回空结果。按以下顺序恢复：\n"
                "1. 换 keyword → 使用实体别名、相关人名、上位词（如'功法'、'法术'）\n"
                "2. 增 context_size 至 3-5 扩大匹配范围\n"
                "3. 如有 chapter_ids，用 SearchCorpus(keyword='新关键词', chapter_ids=[...]) 缩小范围重试\n"
                "4. 切 ReadChapter → ReadChapter(chapter_id=章节号) 读整章\n"
                "5. 连续 3 次空结果 → 必须停止搜索，用已有信息回答\n"
                "禁止用相同参数重复调用。"
            ),
            brief=data.brief or "空结果",
        )

    # 正常返回
    return ToolOk(
        output=output,
        message=(
            "已返回搜索结果。"
            "如需更多上下文: 增大 context_size(如 3-5)。"
            "如需补全信息空白: 使用 ReadChapter(chapter_id=章节号, start=a, end=b) 补全。"
            "如已有足够信息，直接组织回答。"
        ),
        brief=data.brief or "已返回原文",
    )


def format_chapter_read_result(book: str, data: CorpusResult) -> ToolReturnValue:
    """格式化章节读取结果。

    Args:
        book: 书名。
        data: 章节数据。

    Returns:
        格式化后的工具返回值，包含章节内容。
    """
    output = data.to_text()

    # 内容截断
    if len(output) > MAX_CONTENT_LENGTH:
        original_len = len(output)
        output = output[:MAX_CONTENT_LENGTH] + (
            f"\n\n[内容已截断，共 {original_len} 字符，显示前 {MAX_CONTENT_LENGTH} 字符]"
            "\n可使用 start 和 end 参数查看特定范围"
        )
        return ToolOk(
            output=output,
            message=(
                f"章节内容过长已截断（共 {original_len} 字符）。"
                "建议使用 start 和 end 精确读取需要的段落: "
                "ReadChapter(chapter_id=当前章节号, start=起始句, end=结束句)。"
            ),
            brief="内容已截断",
        )

    # 空结果
    if not output:
        return ToolOk(
            output=output,
            message=(
                "未找到该章节内容。请检查 chapter_id 是否正确。"
            ),
            brief=data.brief or "章节未找到",
        )

    # 正常返回：附带章节统计
    # 尝试从 sentences 数量推断总句子数
    total_hint = ""
    if data.sentences:
        total_hint = f"共 {len(data.sentences)} 条句子。"
    elif hasattr(data, 'hint') and data.hint:
        total_hint = ""

    return ToolOk(
        output=output,
        message=(
            f"已返回章节内容。{total_hint}"
            "如需精确读取特定段落: ReadChapter(chapter_id=章节号, start=起始句, end=结束句)。"
            "如已有足够信息，直接组织回答。"
        ),
        brief=data.brief or "已返回章节内容",
    )
