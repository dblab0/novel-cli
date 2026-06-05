"""
报告生成模块。

负责生成评估报告，包括：
- 结构化 JSON 报告
- 可读 Markdown 报告
- 单用例详细记录
- 错误模式自动检测和归因分析

输出目录结构：
    eval_results/<timestamp>/
    ├── report.json          # 结构化结果
    ├── report.md            # 可读报告
    └── cases/
        ├── L1-001.json      # 每个用例的详细记录
        ├── L1-002.json
        └── ...

报告内容：
- 总览：总分、各维度平均分、用例通过率
- 按难度分层统计
- 工具调用统计（全局/分层/用例明细）
- 错误模式统计和归因
- 典型案例（高分案例 + 低分案例）
- 动态改进建议
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from novel_eval.models import CaseResult, EvalScore, ToolCallRecord


# tool_usage 任务的默认维度配置
DEFAULT_TOOL_USAGE_DIMENSIONS = [
    {
        "key": "tool_selection",
        "label": "工具选择",
        "description": "是否选择了正确的工具完成任务",
        "improvement_suggestion": "工具选择能力需要改进，建议加强 action 类型识别训练",
    },
    {
        "key": "param_quality",
        "label": "参数质量",
        "description": "工具调用参数是否完整准确",
        "improvement_suggestion": "参数构造能力需要改进，建议优化 query 提取",
    },
    {
        "key": "call_efficiency",
        "label": "调用链效率",
        "description": "调用链是否高效，有无冗余调用",
        "improvement_suggestion": "调用链效率需要改进，建议减少冗余调用",
    },
    {
        "key": "result_utilization",
        "label": "结果利用",
        "description": "是否有效利用了工具返回的结果",
        "improvement_suggestion": "结果利用能力需要改进，建议加强对工具返回内容的引用",
    },
]

# 错误模式改进建议
_ERROR_PATTERN_SUGGESTIONS = {
    "infinite_loop": (
        "检测到无限循环调用（同一参数重复 ≥5 次）。"
        "建议在 agent system prompt 中强化退出规则："
        "同一参数不得重复调用、3 次空结果后强制停止。"
    ),
    "null_params": (
        "检测到多次空参数调用（必填参数为 null/None）。"
        "建议检查 agent 对工具参数 schema 的理解，"
        "确保必填参数（rel_type、keyword 等）始终有值。"
    ),
    "no_tool_use": (
        "检测到未使用任何工具直接回答。"
        "建议检查 agent 是否正确识别了需要检索的问题，"
        "强化'所有事实性问题必须使用工具'的约束。"
    ),
    "wrong_tool_order": (
        "检测到工具调用顺序违反五步管线。"
        "建议确保 Entity 在 Corpus 之前，"
        "Graph 概览在 Graph 详情之前。"
    ),
    "result_ignored": (
        "检测到连续空结果后仍继续搜索。"
        "建议使用 ReadChapter 作为兜底工具，"
        "或用已有信息组织回答。"
    ),
}

# 维度改进建议
_DIMENSION_SUGGESTIONS = {
    "call_efficiency": (
        "调用效率过低。建议优化五步管线的提前退出策略："
        "简单事实查询在 Graph 概览阶段即可退出。"
    ),
    "result_utilization": (
        "结果利用率低。建议确保每步结果都指导下一步："
        "entity_id→Graph、aliases→Corpus、chapter_ids→范围限定。"
    ),
    "param_quality": (
        "参数质量不足。建议确保 query 为单关键词、"
        "rel_type 从 Graph 概览复制、chapter_ids 来自 Graph 详情。"
    ),
    "format_compliance": (
        "格式合规性不足。建议严格遵循 SKILL.md 模板结构，"
        "不增加模板外章节，确保表格格式正确。"
    ),
}


def generate_report(
    results: list[tuple[CaseResult, EvalScore]],
    report_dir: Path,
    *,
    task_type: str = "tool_usage",
    dimensions: list[dict] | None = None,
) -> Path:
    """生成评估报告。

    生成 JSON + Markdown 格式的评估报告，输出到指定目录。

    Args:
        results: (CaseResult, EvalScore) 元组列表。
        report_dir: 报告输出目录，由 CLI 层决定完整路径。
        task_type: 任务类型，如 "tool_usage" 或 "skill_generation"。
        dimensions: 维度配置列表，每个元素包含 key/label/description/improvement_suggestion。
                    为 None 时使用 DEFAULT_TOOL_USAGE_DIMENSIONS。

    Returns:
        报告目录路径。
    """
    # 未指定维度或为空时使用默认 tool_usage 维度
    if not dimensions:
        dimensions = DEFAULT_TOOL_USAGE_DIMENSIONS

    report_dir.mkdir(parents=True, exist_ok=True)

    # 创建 cases 目录
    cases_dir = report_dir / "cases"
    cases_dir.mkdir(exist_ok=True)

    # 生成 report.json
    report_data = _build_report_json(results, task_type=task_type, dimensions=dimensions)
    with open(report_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    # 生成 report.md
    report_md = _build_report_md(results, report_data, task_type=task_type, dimensions=dimensions)
    with open(report_dir / "report.md", "w", encoding="utf-8") as f:
        f.write(report_md)

    # 生成每个用例的详细 JSON
    for case_result, eval_score in results:
        # 检测错误模式
        error_patterns = _detect_error_patterns(case_result.tool_calls)
        case_data = {
            "case_id": case_result.case_id,
            "question": case_result.question,
            "tool_calls": [
                {
                    "step": tc.step,
                    "tool_name": tc.tool_name,
                    "params": tc.params,
                    "result_summary": tc.result_summary,
                }
                for tc in case_result.tool_calls
            ],
            "final_answer": case_result.final_answer,
            "execution_time": case_result.execution_time,
            "error": case_result.error,
            "error_patterns": error_patterns,
            "score": {
                "scores": eval_score.scores,
                "total_score": eval_score.total_score,
                "commentary": eval_score.commentary,
            },
        }
        case_file = cases_dir / f"{case_result.case_id}.json"
        with open(case_file, "w", encoding="utf-8") as f:
            json.dump(case_data, f, ensure_ascii=False, indent=2)

    return report_dir


def _detect_error_patterns(tool_calls: list[ToolCallRecord]) -> list[str]:
    """检测工具调用中的错误模式。

    识别 5 种常见错误模式：
    - infinite_loop: 同一 (tool, params) 重复 ≥5 次
    - null_params: 必填参数传入 null/None ≥3 次
    - no_tool_use: 0 次工具调用
    - wrong_tool_order: 先 Corpus 后 Entity，或跳过 Graph
    - result_ignored: 连续 3 次以上空结果后仍继续搜索

    Args:
        tool_calls: 工具调用记录列表。

    Returns:
        检测到的错误模式列表。
    """
    patterns = []

    if not tool_calls:
        return ["no_tool_use"]

    # 1. infinite_loop
    seen: Counter[tuple] = Counter()
    for tc in tool_calls:
        key = (tc.tool_name, json.dumps(tc.params, sort_keys=True, ensure_ascii=False))
        seen[key] += 1
    if max(seen.values()) >= 5:
        patterns.append("infinite_loop")

    # 2. null_params
    null_count = sum(
        1 for tc in tool_calls
        if any(v is None for v in tc.params.values())
    )
    if null_count >= 3:
        patterns.append("null_params")

    # 3. wrong_tool_order
    tool_order = [tc.tool_name for tc in tool_calls]
    if "SearchCorpus" in tool_order:
        corpus_idx = tool_order.index("SearchCorpus")
        entity_idx = tool_order.index("SearchEntity") if "SearchEntity" in tool_order else len(tool_order)
        if corpus_idx < entity_idx:
            patterns.append("wrong_tool_order")

    # 4. result_ignored（连续 3 次以上空结果后仍继续搜索）
    consecutive_empty = 0
    for tc in tool_calls:
        if not tc.result_summary:
            consecutive_empty += 1
        else:
            consecutive_empty = 0
        if consecutive_empty >= 3:
            patterns.append("result_ignored")
            break

    return patterns


def _compute_case_tool_stats(tool_calls: list[ToolCallRecord]) -> dict[str, Any]:
    """计算单个 case 的工具调用统计。

    Args:
        tool_calls: 工具调用记录列表。

    Returns:
        工具统计字典，包含 total_calls / valid_calls / duplicate_calls /
        unique_tools_count / tool_distribution。
    """
    total_calls = len(tool_calls)
    valid_calls = sum(1 for tc in tool_calls if tc.result_summary)
    tool_counter: Counter[str] = Counter(tc.tool_name for tc in tool_calls)

    # 重复调用：同 case 内 (tool_name, params) 完全一致的调用，仅统计超出首次的部分
    seen: Counter[tuple[str, str]] = Counter()
    duplicate_calls = 0
    for tc in tool_calls:
        key = (tc.tool_name, json.dumps(tc.params, sort_keys=True, ensure_ascii=False))
        seen[key] += 1
        if seen[key] > 1:
            duplicate_calls += 1

    # 工具分布
    tool_distribution: dict[str, dict[str, Any]] = {}
    for tool_name, count in tool_counter.most_common():
        tool_distribution[tool_name] = {
            "count": count,
            "percentage": round(count / total_calls * 100, 1) if total_calls > 0 else 0.0,
        }

    return {
        "total_calls": total_calls,
        "valid_calls": valid_calls,
        "duplicate_calls": duplicate_calls,
        "unique_tools_count": len(tool_counter),
        "tool_distribution": tool_distribution,
    }


def _aggregate_tool_stats(
    case_stats_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """将多个 case 的工具统计聚合为全局统计。

    Args:
        case_stats_list: 多个 case 的工具统计字典列表。

    Returns:
        聚合后的统计字典。
    """
    total_calls = sum(s["total_calls"] for s in case_stats_list)
    valid_calls = sum(s["valid_calls"] for s in case_stats_list)
    duplicate_calls = sum(s["duplicate_calls"] for s in case_stats_list)

    # 合并工具分布
    merged_counter: Counter[str] = Counter()
    for s in case_stats_list:
        for tool_name, info in s["tool_distribution"].items():
            merged_counter[tool_name] += info["count"]

    tool_distribution: dict[str, dict[str, Any]] = {}
    for tool_name, count in merged_counter.most_common():
        tool_distribution[tool_name] = {
            "count": count,
            "percentage": round(count / total_calls * 100, 1) if total_calls > 0 else 0.0,
        }

    return {
        "total_calls": total_calls,
        "valid_calls": valid_calls,
        "duplicate_calls": duplicate_calls,
        "unique_tools_count": len(merged_counter),
        "tool_distribution": tool_distribution,
    }


def _extract_level(case_id: str) -> str:
    """从 case_id 中提取难度等级或技能类型。

    支持两种格式：
    - tool_usage: "L{level}-{seq}" → 提取 L 后面的数字，如 "L1-001" → "1"
    - skill_generation: "SG-{type}-{seq}" → 提取 type，如 "SG-character-001" → "character"
    无法解析时返回 "unknown"。

    Args:
        case_id: 用例 ID，如 "L1-001" 或 "SG-character-001"。

    Returns:
        难度等级或技能类型字符串。
    """
    parts = case_id.split("-")
    if parts and parts[0].startswith("L"):
        return parts[0][1:]
    if parts and parts[0] == "SG" and len(parts) >= 2:
        return parts[1]
    return "unknown"


def _build_report_json(
    results: list[tuple[CaseResult, EvalScore]],
    *,
    task_type: str,
    dimensions: list[dict],
) -> dict[str, Any]:
    """构建 report.json 结构。

    包含：
    - task_type: 任务类型
    - dimensions: 维度配置
    - total_cases: 用例总数
    - total_score: 总平均分
    - dimension_scores: 各维度平均分
    - pass_rate: 通过率（总分 >= 3 为通过）
    - tool_stats: 工具调用统计（全局 + 分层）
    - error_patterns_summary: 错误模式汇总
    - cases_summary: 每个用例的摘要（含工具统计和错误模式）

    Args:
        results: 用例结果和评分列表。
        task_type: 任务类型。
        dimensions: 维度配置列表。

    Returns:
        报告数据字典。
    """
    # 从 dimensions 推导维度 key 列表
    dim_keys = [d["key"] for d in dimensions]

    if not results:
        return {
            "task_type": task_type,
            "dimensions": dimensions,
            "total_cases": 0,
            "total_score": 0.0,
            "dimension_scores": {},
            "pass_rate": 0.0,
            "tool_stats": {"global": _aggregate_tool_stats([]), "per_level": {}},
            "error_patterns_summary": {},
            "cases_summary": [],
        }

    total_score = sum(s.total_score for _, s in results) / len(results)
    passed = sum(1 for _, s in results if s.total_score >= 3.0)
    pass_rate = passed / len(results)

    # 计算各维度平均分
    dimension_scores = {}
    for dim in dim_keys:
        scores = [s.scores.get(dim, 0) for _, s in results]
        dimension_scores[dim] = sum(scores) / len(scores) if scores else 0.0

    # 计算每个 case 的工具统计和错误模式
    all_case_stats: list[dict[str, Any]] = []
    cases_summary = []
    error_patterns_agg: dict[str, list[str]] = {}

    for cr, es in results:
        case_stats = _compute_case_tool_stats(cr.tool_calls)
        all_case_stats.append(case_stats)
        error_patterns = _detect_error_patterns(cr.tool_calls)

        # 聚合错误模式
        for pattern in error_patterns:
            error_patterns_agg.setdefault(pattern, []).append(cr.case_id)

        cases_summary.append({
            "case_id": cr.case_id,
            "question": cr.question,
            "execution_time": cr.execution_time,
            "total_score": round(es.total_score, 2),
            "passed": es.total_score >= 3.0,
            "tool_stats": case_stats,
            "error_patterns": error_patterns,
        })

    # 构建错误模式汇总
    error_patterns_summary = {
        pattern: {
            "count": len(affected),
            "affected_cases": affected,
        }
        for pattern, affected in error_patterns_agg.items()
    }

    # 全局工具统计
    global_stats = _aggregate_tool_stats(all_case_stats)

    # 按 level 分组统计
    per_level: dict[str, list[dict[str, Any]]] = {}
    for i, (cr, _) in enumerate(results):
        level = _extract_level(cr.case_id)
        per_level.setdefault(level, []).append(all_case_stats[i])

    per_level_stats = {
        level: _aggregate_tool_stats(stats_list)
        for level, stats_list in per_level.items()
    }

    return {
        "task_type": task_type,
        "dimensions": dimensions,
        "total_cases": len(results),
        "total_score": round(total_score, 2),
        "dimension_scores": {k: round(v, 2) for k, v in dimension_scores.items()},
        "pass_rate": round(pass_rate, 4),
        "tool_stats": {
            "global": global_stats,
            "per_level": per_level_stats,
        },
        "error_patterns_summary": error_patterns_summary,
        "cases_summary": cases_summary,
    }


def _build_report_md(
    results: list[tuple[CaseResult, EvalScore]],
    report_data: dict[str, Any],
    *,
    task_type: str,
    dimensions: list[dict],
) -> str:
    """构建 report.md 内容。

    包含：
    - 总览：总分、各维度平均分、通过率
    - 按难度分层统计
    - 工具调用统计（全局 + 分层 + 用例明细）
    - 错误模式统计
    - 典型案例（高分 + 低分）
    - 低分案例深度归因
    - 能力维度评估
    - 动态改进建议

    Args:
        results: 用例结果和评分列表。
        report_data: 已构建的报告 JSON 数据。
        task_type: 任务类型。
        dimensions: 维度配置列表。

    Returns:
        Markdown 格式的报告内容。
    """
    # 从 dimensions 参数构建标签映射和改进建议映射
    dim_labels = {d["key"]: d["label"] for d in dimensions}

    lines = []

    # 标题：根据任务类型动态生成
    lines.append(f"# Novel Agent {task_type} 评估报告")
    lines.append("")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 总览
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- **用例总数**: {report_data['total_cases']}")
    lines.append(f"- **平均总分**: {report_data['total_score']:.2f}")
    lines.append(f"- **通过率**: {report_data['pass_rate']:.1%}")
    lines.append("")

    # 各维度平均分
    lines.append("## 各维度平均分")
    lines.append("")
    lines.append("| 维度 | 平均分 |")
    lines.append("|------|--------|")
    for dim, score in report_data["dimension_scores"].items():
        dim_name = dim_labels.get(dim, dim)
        lines.append(f"| {dim_name} | {score:.2f} |")
    lines.append("")

    # 按难度分层统计
    lines.append("## 按难度分层统计")
    lines.append("")

    if results:
        level_groups: dict[str, list[tuple[CaseResult, EvalScore]]] = {}
        for cr, es in results:
            level = _extract_level(cr.case_id)
            level_groups.setdefault(level, []).append((cr, es))

        if level_groups:
            lines.append("| 难度 | 用例数 | 平均总分 | 通过率 |")
            lines.append("|------|--------|----------|--------|")
            for level in sorted(level_groups.keys()):
                group = level_groups[level]
                count = len(group)
                avg_score = sum(es.total_score for _, es in group) / count
                passed = sum(1 for _, es in group if es.total_score >= 3.0)
                pass_rate = passed / count
                lines.append(f"| Level {level} | {count} | {avg_score:.2f} | {pass_rate:.0%} |")
            lines.append("")
        else:
            lines.append("无法提取难度等级信息")
            lines.append("")
    else:
        lines.append("无数据")
        lines.append("")

    # 工具调用统计
    tool_stats = report_data.get("tool_stats", {})
    global_stats = tool_stats.get("global", {})

    lines.append("## 工具调用统计")
    lines.append("")

    gs = global_stats
    if gs and gs.get("total_calls", 0) > 0:
        total = gs["total_calls"]
        valid = gs["valid_calls"]
        dup = gs["duplicate_calls"]
        valid_pct = f"{valid / total * 100:.1f}%" if total else "0%"
        dup_pct = f"{dup / total * 100:.1f}%" if total else "0%"

        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 总调用次数 | {total} |")
        lines.append(f"| 有效调用 | {valid} ({valid_pct}) |")
        lines.append(f"| 重复调用 | {dup} ({dup_pct}) |")
        lines.append(f"| 使用工具种类 | {gs['unique_tools_count']} |")
        lines.append("")

        # 工具调用分布
        dist = gs.get("tool_distribution", {})
        if dist:
            lines.append("### 工具调用分布")
            lines.append("")
            lines.append("| 工具 | 调用次数 | 占比 |")
            lines.append("|------|----------|------|")
            for tool_name, info in dist.items():
                lines.append(f"| {tool_name} | {info['count']} | {info['percentage']:.1f}% |")
            lines.append("")

        # 按难度分层工具统计
        per_level = tool_stats.get("per_level", {})
        if per_level:
            lines.append("### 按难度分层")
            lines.append("")
            lines.append("| 难度 | 总调用 | 有效 | 重复 | 工具种类 |")
            lines.append("|------|--------|------|------|----------|")
            for level in sorted(per_level.keys()):
                ls = per_level[level]
                lt = ls["total_calls"]
                lv = ls["valid_calls"]
                ld = ls["duplicate_calls"]
                lv_pct = f"{lv / lt * 100:.1f}%" if lt else "0%"
                ld_pct = f"{ld / lt * 100:.1f}%" if lt else "0%"
                lines.append(f"| Level {level} | {lt} | {lv} ({lv_pct}) | {ld} ({ld_pct}) | {ls['unique_tools_count']} |")
            lines.append("")
    else:
        lines.append("无工具调用数据")
        lines.append("")

    # 错误模式统计
    error_patterns_summary = report_data.get("error_patterns_summary", {})
    lines.append("## 错误模式统计")
    lines.append("")

    if error_patterns_summary:
        lines.append("| 错误模式 | 受影响用例数 | 受影响用例 |")
        lines.append("|----------|-------------|-----------|")
        for pattern, info in error_patterns_summary.items():
            cases_str = ", ".join(info["affected_cases"])
            lines.append(f"| {pattern} | {info['count']} | {cases_str} |")
        lines.append("")
    else:
        lines.append("无异常模式检出")
        lines.append("")

    # 典型案例
    lines.append("## 典型案例")
    lines.append("")

    if results:
        # 高分案例
        high_score_cases = sorted(results, key=lambda x: x[1].total_score, reverse=True)[:3]
        lines.append("### 高分案例")
        lines.append("")
        for cr, es in high_score_cases:
            lines.append(f"- **{cr.case_id}**: {es.total_score:.2f} 分")
            lines.append(f"  - 问题: {cr.question}")
            lines.append("")

        # 低分案例
        low_score_cases = sorted(results, key=lambda x: x[1].total_score)[:3]
        lines.append("### 低分案例")
        lines.append("")
        for cr, es in low_score_cases:
            lines.append(f"- **{cr.case_id}**: {es.total_score:.2f} 分")
            lines.append(f"  - 问题: {cr.question}")
            if es.commentary:
                lines.append(f"  - 评语: {es.commentary}")
            lines.append("")

    # 用例工具调用明细
    lines.append("## 用例工具调用明细")
    lines.append("")

    if report_data.get("cases_summary"):
        lines.append("| 用例 | 总调用 | 有效 | 重复 | 工具种类 | 主要工具 | 错误模式 |")
        lines.append("|------|--------|------|------|----------|----------|----------|")
        for case_summary in report_data["cases_summary"]:
            cs = case_summary.get("tool_stats", {})
            tc = cs.get("total_calls", 0)
            vc = cs.get("valid_calls", 0)
            dc = cs.get("duplicate_calls", 0)
            ut = cs.get("unique_tools_count", 0)
            v_pct = f"{vc / tc * 100:.0f}%" if tc else "0%"
            d_val = f"{dc} ({dc / tc * 100:.0f}%)" if tc else "0"

            # 主要工具：按调用次数降序，格式 "工具名×次数"
            dist = cs.get("tool_distribution", {})
            tools_str = ", ".join(
                f"{name}×{info['count']}"
                for name, info in dist.items()
            )

            # 错误模式
            patterns = case_summary.get("error_patterns", [])
            patterns_str = ", ".join(patterns) if patterns else "-"

            lines.append(f"| {case_summary['case_id']} | {tc} | {vc} ({v_pct}) | {d_val} | {ut} | {tools_str} | {patterns_str} |")
        lines.append("")

    # 低分案例深度归因
    lines.append("## 低分案例深度归因")
    lines.append("")

    low_score_threshold = 3.0
    low_cases = [(cr, es) for cr, es in results if es.total_score < low_score_threshold]
    if low_cases:
        for cr, es in sorted(low_cases, key=lambda x: x[1].total_score):
            error_patterns = _detect_error_patterns(cr.tool_calls)
            case_stats = _compute_case_tool_stats(cr.tool_calls)
            total = case_stats["total_calls"]
            dup = case_stats["duplicate_calls"]
            dup_pct = f"{dup / total * 100:.1f}%" if total else "0%"

            lines.append(f"### {cr.case_id} ({es.total_score:.2f}分)")
            lines.append(f"- **问题**: {cr.question}")
            lines.append(f"- **错误模式**: {', '.join(error_patterns) if error_patterns else '无'}")
            lines.append(f"- **调用统计**: {total} 次调用，{dup} 次重复（{dup_pct}）")

            # 找到最低分维度
            if es.scores:
                worst_dim = min(es.scores, key=lambda k: es.scores[k])
                worst_score = es.scores[worst_dim]
                dim_name = dim_labels.get(worst_dim, worst_dim)
                lines.append(f"- **最弱维度**: {dim_name}（{worst_score} 分）")

            if es.commentary:
                lines.append(f"- **评语**: {es.commentary}")

            # 根据错误模式生成改进建议
            for pattern in error_patterns:
                if pattern in _ERROR_PATTERN_SUGGESTIONS:
                    lines.append(f"- **改进建议**（{pattern}）: {_ERROR_PATTERN_SUGGESTIONS[pattern]}")
            lines.append("")
    else:
        lines.append("所有用例得分均 ≥ 3.0，无低分案例需要归因分析。")
        lines.append("")

    # 能力维度评估
    lines.append("## 能力维度评估")
    lines.append("")

    if report_data.get("dimension_scores"):
        for dim, score in report_data["dimension_scores"].items():
            dim_name = dim_labels.get(dim, dim)
            if score >= 4.0:
                tag = "强项"
            elif score >= 3.0:
                tag = "待改进"
            else:
                tag = "弱项"
            lines.append(f"- **{dim_name}**: {score:.2f} 分 — {tag}")
        lines.append("")

    # 动态改进建议
    lines.append("## 改进建议")
    lines.append("")

    suggestions = []

    # 优先展示错误模式相关的建议
    if error_patterns_summary:
        for pattern in error_patterns_summary:
            if pattern in _ERROR_PATTERN_SUGGESTIONS:
                suggestions.append(f"- {_ERROR_PATTERN_SUGGESTIONS[pattern]}")

    # 其次展示低于 3.0 分的维度建议
    for dim, score in report_data.get("dimension_scores", {}).items():
        if score < 3.0 and dim in _DIMENSION_SUGGESTIONS:
            suggestions.append(f"- {_DIMENSION_SUGGESTIONS[dim]}")

    if suggestions:
        lines.extend(suggestions)
    else:
        lines.append("- 各维度表现良好，继续保持")

    lines.append("")

    return "\n".join(lines)
