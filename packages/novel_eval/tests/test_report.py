"""报告生成模块测试。"""

import json
from pathlib import Path

import pytest

from novel_eval.models import CaseResult, EvalScore, ToolCallRecord
from novel_eval.report import (
    DEFAULT_TOOL_USAGE_DIMENSIONS,
    _aggregate_tool_stats,
    _build_report_json,
    _build_report_md,
    _compute_case_tool_stats,
    _extract_level,
    generate_report,
)

# 测试中统一使用的 task_type 和 dimensions 参数
_TU_TASK_TYPE = "tool_usage"
_TU_DIMENSIONS = DEFAULT_TOOL_USAGE_DIMENSIONS


def _build_json(results):
    """测试辅助：使用默认 tool_usage 参数构建 report.json。"""
    return _build_report_json(results, task_type=_TU_TASK_TYPE, dimensions=_TU_DIMENSIONS)


def _build_md(results, report_data):
    """测试辅助：使用默认 tool_usage 参数构建 report.md。"""
    return _build_report_md(results, report_data, task_type=_TU_TASK_TYPE, dimensions=_TU_DIMENSIONS)


class TestGenerateReport:
    """generate_report 函数测试。"""

    def test_generate_report_structure(self, tmp_path: Path) -> None:
        """测试报告目录结构正确生成。"""
        results = [
            (
                CaseResult(
                    case_id="L1-001",
                    question="韩立是谁？",
                    tool_calls=[],
                    final_answer="韩立是男主角",
                    execution_time=1.0,
                ),
                EvalScore(
                    case_id="L1-001",
                    scores={
                        "tool_selection": 4,
                        "param_quality": 4,
                        "call_efficiency": 4,
                        "result_utilization": 4,
                    },
                    total_score=4.0,
                    commentary="表现优秀",
                ),
            ),
        ]

        report_dir = tmp_path / "凡人修仙传" / "2026-04-27_120000"
        generate_report(results, report_dir)

        assert report_dir.exists()
        assert (report_dir / "report.json").exists()
        assert (report_dir / "report.md").exists()
        assert (report_dir / "cases" / "L1-001.json").exists()

    def test_generate_report_with_tool_calls(self, tmp_path: Path) -> None:
        """测试包含工具调用记录的报告生成。"""
        results = [
            (
                CaseResult(
                    case_id="L1-002",
                    question="韩立的师父是谁？",
                    tool_calls=[
                        ToolCallRecord(
                            step=1,
                            tool_name="SearchEntity",
                            params={"query": "韩立", "entity_type": "人物"},
                            result_summary="找到人物：韩立",
                        ),
                        ToolCallRecord(
                            step=2,
                            tool_name="SearchGraph",
                            params={"entity": "韩立", "relation": "师父"},
                            result_summary="马良",
                        ),
                    ],
                    final_answer="韩立的师父是马良",
                    execution_time=2.5,
                ),
                EvalScore(
                    case_id="L1-002",
                    scores={
                        "tool_selection": 5,
                        "param_quality": 5,
                        "call_efficiency": 4,
                        "result_utilization": 5,
                    },
                    total_score=4.75,
                    commentary="工具调用正确",
                ),
            ),
        ]

        report_dir = tmp_path / "test_report"
        generate_report(results, report_dir)

        with open(report_dir / "cases" / "L1-002.json") as f:
            case_data = json.load(f)

        assert len(case_data["tool_calls"]) == 2
        assert case_data["tool_calls"][0]["tool_name"] == "SearchEntity"
        assert case_data["tool_calls"][1]["tool_name"] == "SearchGraph"
        assert case_data["execution_time"] == 2.5


class TestComputeToolStats:
    """_compute_case_tool_stats 函数测试。"""

    def test_empty_tool_calls(self) -> None:
        """测试无工具调用时统计全为 0。"""
        stats = _compute_case_tool_stats([])

        assert stats["total_calls"] == 0
        assert stats["valid_calls"] == 0
        assert stats["duplicate_calls"] == 0
        assert stats["unique_tools_count"] == 0
        assert stats["tool_distribution"] == {}

    def test_valid_calls_count(self) -> None:
        """测试部分 result_summary 为空时 valid_calls 计数正确。"""
        tool_calls = [
            ToolCallRecord(step=1, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="找到韩立"),
            ToolCallRecord(step=2, tool_name="SearchGraph", params={"entity": "韩立"}, result_summary=""),
            ToolCallRecord(step=3, tool_name="SearchCorpus", params={"query": "韩立"}, result_summary="相关内容"),
        ]

        stats = _compute_case_tool_stats(tool_calls)

        assert stats["total_calls"] == 3
        assert stats["valid_calls"] == 2

    def test_duplicate_calls_count(self) -> None:
        """测试同 case 内 tool_name+params 完全一致计为重复。"""
        tool_calls = [
            ToolCallRecord(step=1, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="结果1"),
            ToolCallRecord(step=2, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="结果2"),
            ToolCallRecord(step=3, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="结果3"),
        ]

        stats = _compute_case_tool_stats(tool_calls)

        assert stats["total_calls"] == 3
        assert stats["duplicate_calls"] == 2  # 第 2、3 次是重复

    def test_duplicate_different_params(self) -> None:
        """测试 tool_name 相同但 params 不同不计为重复。"""
        tool_calls = [
            ToolCallRecord(step=1, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="结果1"),
            ToolCallRecord(step=2, tool_name="SearchEntity", params={"query": "马良"}, result_summary="结果2"),
        ]

        stats = _compute_case_tool_stats(tool_calls)

        assert stats["total_calls"] == 2
        assert stats["duplicate_calls"] == 0

    def test_tool_distribution(self) -> None:
        """测试工具分布统计正确。"""
        tool_calls = [
            ToolCallRecord(step=1, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="结果"),
            ToolCallRecord(step=2, tool_name="SearchEntity", params={"query": "马良"}, result_summary="结果"),
            ToolCallRecord(step=3, tool_name="SearchGraph", params={"entity": "韩立"}, result_summary="结果"),
        ]

        stats = _compute_case_tool_stats(tool_calls)

        assert stats["unique_tools_count"] == 2
        assert stats["tool_distribution"]["SearchEntity"]["count"] == 2
        assert stats["tool_distribution"]["SearchEntity"]["percentage"] == 66.7
        assert stats["tool_distribution"]["SearchGraph"]["count"] == 1
        assert stats["tool_distribution"]["SearchGraph"]["percentage"] == 33.3


class TestAggregateToolStats:
    """_aggregate_tool_stats 函数测试。"""

    def test_aggregate_empty(self) -> None:
        """测试空列表聚合。"""
        stats = _aggregate_tool_stats([])

        assert stats["total_calls"] == 0
        assert stats["valid_calls"] == 0
        assert stats["duplicate_calls"] == 0
        assert stats["unique_tools_count"] == 0

    def test_aggregate_multiple_cases(self) -> None:
        """测试多个 case 聚合统计。"""
        case_stats_list = [
            {
                "total_calls": 3,
                "valid_calls": 2,
                "duplicate_calls": 1,
                "unique_tools_count": 2,
                "tool_distribution": {
                    "SearchEntity": {"count": 2, "percentage": 66.7},
                    "SearchGraph": {"count": 1, "percentage": 33.3},
                },
            },
            {
                "total_calls": 2,
                "valid_calls": 2,
                "duplicate_calls": 0,
                "unique_tools_count": 1,
                "tool_distribution": {
                    "SearchEntity": {"count": 2, "percentage": 100.0},
                },
            },
        ]

        stats = _aggregate_tool_stats(case_stats_list)

        assert stats["total_calls"] == 5
        assert stats["valid_calls"] == 4
        assert stats["duplicate_calls"] == 1
        assert stats["unique_tools_count"] == 2
        assert stats["tool_distribution"]["SearchEntity"]["count"] == 4
        assert stats["tool_distribution"]["SearchGraph"]["count"] == 1


class TestBuildReportJson:
    """_build_report_json 函数测试。"""

    def test_empty_results(self) -> None:
        """测试空结果列表。"""
        data = _build_json([])

        assert data["total_cases"] == 0
        assert data["total_score"] == 0.0
        assert data["dimension_scores"] == {}
        assert data["pass_rate"] == 0.0
        assert "tool_stats" in data
        # 空 results 时也要包含 task_type 和 dimensions
        assert data["task_type"] == "tool_usage"
        assert data["dimensions"] == _TU_DIMENSIONS

    def test_single_result(self) -> None:
        """测试单个结果。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="测试", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={
                        "tool_selection": 5,
                        "param_quality": 4,
                        "call_efficiency": 3,
                        "result_utilization": 4,
                    },
                    total_score=4.0,
                    commentary="",
                ),
            ),
        ]

        data = _build_json(results)

        assert data["total_cases"] == 1
        assert data["total_score"] == 4.0
        assert data["pass_rate"] == 1.0  # 4.0 >= 3.0
        assert len(data["cases_summary"]) == 1
        # 工具统计
        assert data["tool_stats"]["global"]["total_calls"] == 0
        assert "tool_stats" in data["cases_summary"][0]

    def test_multiple_results_average_score(self) -> None:
        """测试多个结果的平均分计算。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="测试1", tool_calls=[], final_answer="回答1", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={
                        "tool_selection": 5,
                        "param_quality": 5,
                        "call_efficiency": 5,
                        "result_utilization": 5,
                    },
                    total_score=5.0,
                    commentary="",
                ),
            ),
            (
                CaseResult(case_id="L1-002", question="测试2", tool_calls=[], final_answer="回答2", execution_time=2.0),
                EvalScore(
                    case_id="L1-002",
                    scores={
                        "tool_selection": 1,
                        "param_quality": 1,
                        "call_efficiency": 1,
                        "result_utilization": 1,
                    },
                    total_score=1.0,
                    commentary="需要改进",
                ),
            ),
        ]

        data = _build_json(results)

        assert data["total_cases"] == 2
        assert data["total_score"] == 3.0  # (5.0 + 1.0) / 2
        assert data["pass_rate"] == 0.5  # 1 passed out of 2

    def test_dimension_scores_average(self) -> None:
        """测试各维度平均分计算。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="测试1", tool_calls=[], final_answer="回答1", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={
                        "tool_selection": 4,
                        "param_quality": 4,
                        "call_efficiency": 4,
                        "result_utilization": 4,
                    },
                    total_score=4.0,
                    commentary="",
                ),
            ),
            (
                CaseResult(case_id="L1-002", question="测试2", tool_calls=[], final_answer="回答2", execution_time=2.0),
                EvalScore(
                    case_id="L1-002",
                    scores={
                        "tool_selection": 2,
                        "param_quality": 2,
                        "call_efficiency": 2,
                        "result_utilization": 2,
                    },
                    total_score=2.0,
                    commentary="",
                ),
            ),
        ]

        data = _build_json(results)

        # 各维度平均分应为 3.0
        for dim in ["tool_selection", "param_quality", "call_efficiency", "result_utilization"]:
            assert data["dimension_scores"][dim] == 3.0

    def test_tool_stats_per_level(self) -> None:
        """测试按 level 分组的工具统计。"""
        results = [
            (
                CaseResult(
                    case_id="L1-001",
                    question="测试",
                    tool_calls=[
                        ToolCallRecord(step=1, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="结果"),
                    ],
                    final_answer="回答",
                    execution_time=1.0,
                ),
                EvalScore(case_id="L1-001", scores={"tool_selection": 5, "param_quality": 5, "call_efficiency": 5, "result_utilization": 5}, total_score=5.0, commentary=""),
            ),
            (
                CaseResult(
                    case_id="L2-001",
                    question="测试",
                    tool_calls=[
                        ToolCallRecord(step=1, tool_name="SearchGraph", params={"entity": "韩立"}, result_summary="结果"),
                        ToolCallRecord(step=2, tool_name="SearchCorpus", params={"query": "韩立"}, result_summary="结果"),
                    ],
                    final_answer="回答",
                    execution_time=2.0,
                ),
                EvalScore(case_id="L2-001", scores={"tool_selection": 4, "param_quality": 4, "call_efficiency": 4, "result_utilization": 4}, total_score=4.0, commentary=""),
            ),
        ]

        data = _build_json(results)

        # 全局统计
        assert data["tool_stats"]["global"]["total_calls"] == 3
        assert data["tool_stats"]["global"]["unique_tools_count"] == 3

        # 分层统计
        assert "1" in data["tool_stats"]["per_level"]
        assert "2" in data["tool_stats"]["per_level"]
        assert data["tool_stats"]["per_level"]["1"]["total_calls"] == 1
        assert data["tool_stats"]["per_level"]["2"]["total_calls"] == 2


class TestBuildReportMd:
    """_build_report_md 函数测试。"""

    def test_report_md_contains_required_sections(self) -> None:
        """测试 Markdown 报告包含必需章节。"""
        results = [
            (
                CaseResult(
                    case_id="L1-001",
                    question="韩立是谁？",
                    tool_calls=[],
                    final_answer="韩立是男主角",
                    execution_time=1.0,
                ),
                EvalScore(
                    case_id="L1-001",
                    scores={
                        "tool_selection": 5,
                        "param_quality": 5,
                        "call_efficiency": 5,
                        "result_utilization": 5,
                    },
                    total_score=5.0,
                    commentary="完美",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        assert "# Novel Agent tool_usage 评估报告" in content
        assert "## 总览" in content
        assert "## 各维度平均分" in content
        assert "## 典型案例" in content
        assert "## 改进建议" in content
        assert "## 工具调用统计" in content
        assert "## 用例工具调用明细" in content

    def test_report_md_tool_stats_section(self) -> None:
        """测试工具调用统计章节内容。"""
        results = [
            (
                CaseResult(
                    case_id="L1-001",
                    question="测试",
                    tool_calls=[
                        ToolCallRecord(step=1, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="找到韩立"),
                        ToolCallRecord(step=2, tool_name="SearchEntity", params={"query": "韩立"}, result_summary=""),
                        ToolCallRecord(step=3, tool_name="SearchGraph", params={"entity": "韩立"}, result_summary="关系结果"),
                    ],
                    final_answer="回答",
                    execution_time=1.0,
                ),
                EvalScore(
                    case_id="L1-001",
                    scores={"tool_selection": 4, "param_quality": 4, "call_efficiency": 4, "result_utilization": 4},
                    total_score=4.0,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        assert "## 工具调用统计" in content
        assert "| 总调用次数 | 3 |" in content
        assert "| 有效调用 | 2 (66.7%) |" in content
        assert "| 重复调用 | 1 (33.3%) |" in content
        assert "| 使用工具种类 | 2 |" in content
        assert "### 工具调用分布" in content
        assert "| SearchEntity | 2 |" in content
        assert "| SearchGraph | 1 |" in content

    def test_report_md_case_detail_table(self) -> None:
        """测试用例工具调用明细表格。"""
        results = [
            (
                CaseResult(
                    case_id="L1-001",
                    question="测试",
                    tool_calls=[
                        ToolCallRecord(step=1, tool_name="SearchEntity", params={"query": "韩立"}, result_summary="结果"),
                        ToolCallRecord(step=2, tool_name="SearchGraph", params={"entity": "韩立"}, result_summary="关系"),
                    ],
                    final_answer="回答",
                    execution_time=1.0,
                ),
                EvalScore(
                    case_id="L1-001",
                    scores={"tool_selection": 4, "param_quality": 4, "call_efficiency": 4, "result_utilization": 4},
                    total_score=4.0,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        assert "## 用例工具调用明细" in content
        assert "| L1-001 | 2 | 2 (100%) | 0 (0%) | 2 |" in content
        assert "SearchEntity×1, SearchGraph×1" in content

    def test_report_md_dimension_table(self) -> None:
        """测试 Markdown 报告维度表格格式。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="测试", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={
                        "tool_selection": 4,
                        "param_quality": 3,
                        "call_efficiency": 5,
                        "result_utilization": 4,
                    },
                    total_score=4.0,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        assert "| 维度 | 平均分 |" in content
        assert "|------|--------|" in content
        assert "工具选择" in content
        assert "参数质量" in content
        assert "调用链效率" in content
        assert "结果利用" in content

    def test_report_md_high_low_cases(self) -> None:
        """测试 Markdown 报告展示高低分案例。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="高分问题", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={"tool_selection": 5, "param_quality": 5, "call_efficiency": 5, "result_utilization": 5},
                    total_score=5.0,
                    commentary="优秀",
                ),
            ),
            (
                CaseResult(case_id="L1-002", question="低分问题", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-002",
                    scores={"tool_selection": 1, "param_quality": 1, "call_efficiency": 1, "result_utilization": 1},
                    total_score=1.0,
                    commentary="需要改进",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        assert "### 高分案例" in content
        assert "### 低分案例" in content
        assert "L1-001" in content
        assert "L1-002" in content

    def test_report_md_improvement_suggestions(self) -> None:
        """测试 Markdown 报告包含动态改进建议。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="测试", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={
                        "tool_selection": 2,  # 低分维度
                        "param_quality": 5,
                        "call_efficiency": 5,
                        "result_utilization": 5,
                    },
                    total_score=4.25,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        # 应该包含错误模式建议（无工具调用触发 no_tool_use 模式）
        assert "未使用任何工具" in content
        # 应该包含改进建议章节
        assert "## 改进建议" in content

    def test_report_md_all_dimensions_good(self) -> None:
        """测试所有维度表现良好且无错误模式时的提示。"""
        results = [
            (
                CaseResult(
                    case_id="L1-001", question="测试",
                    tool_calls=[ToolCallRecord(step=1, tool_name="SearchEntity", params={"query": "测试"}, result_summary="找到实体")],
                    final_answer="回答", execution_time=1.0,
                ),
                EvalScore(
                    case_id="L1-001",
                    scores={
                        "tool_selection": 5,
                        "param_quality": 5,
                        "call_efficiency": 5,
                        "result_utilization": 5,
                    },
                    total_score=5.0,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        assert "各维度表现良好，继续保持" in content

    def test_report_md_question_no_truncation(self) -> None:
        """测试长问题文本完整展示，不做截断。"""
        long_question = "这是一个非常长的问题，用于测试文本截断功能是否正常工作" * 3
        results = [
            (
                CaseResult(case_id="L1-001", question=long_question, tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={"tool_selection": 4, "param_quality": 4, "call_efficiency": 4, "result_utilization": 4},
                    total_score=4.0,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        # 问题应完整展示，不做截断
        assert long_question in content


class TestConstants:
    """常量测试。"""

    def test_default_dimensions_completeness(self) -> None:
        """测试 DEFAULT_TOOL_USAGE_DIMENSIONS 包含所有必要字段。"""
        expected_keys = ["tool_selection", "param_quality", "call_efficiency", "result_utilization"]
        actual_keys = [d["key"] for d in DEFAULT_TOOL_USAGE_DIMENSIONS]
        for key in expected_keys:
            assert key in actual_keys

        for d in DEFAULT_TOOL_USAGE_DIMENSIONS:
            assert "key" in d
            assert "label" in d
            assert "description" in d
            assert "improvement_suggestion" in d
            assert isinstance(d["label"], str)
            assert isinstance(d["improvement_suggestion"], str)

    def test_default_dimensions_labels(self) -> None:
        """测试 DEFAULT_TOOL_USAGE_DIMENSIONS 标签映射与旧常量一致。"""
        dim_labels = {d["key"]: d["label"] for d in DEFAULT_TOOL_USAGE_DIMENSIONS}
        assert dim_labels["tool_selection"] == "工具选择"
        assert dim_labels["param_quality"] == "参数质量"
        assert dim_labels["call_efficiency"] == "调用链效率"
        assert dim_labels["result_utilization"] == "结果利用"


class TestExtractLevel:
    """_extract_level 辅助函数测试。"""

    def test_extract_level_standard_format(self) -> None:
        """测试标准 case_id 格式提取。"""
        assert _extract_level("L1-001") == "1"
        assert _extract_level("L2-003") == "2"
        assert _extract_level("L3-010") == "3"
        assert _extract_level("L4-099") == "4"

    def test_extract_level_multi_digit(self) -> None:
        """测试多位数字等级提取。"""
        assert _extract_level("L10-001") == "10"

    def test_extract_level_unknown_format(self) -> None:
        """测试无法解析的格式返回 unknown。"""
        assert _extract_level("unknown-id") == "unknown"
        assert _extract_level("test") == "unknown"

    def test_extract_level_skill_generation_format(self) -> None:
        """测试 skill_generation 的 SG-{type}-{seq} 格式。"""
        assert _extract_level("SG-character-001") == "character"
        assert _extract_level("SG-faction-003") == "faction"
        assert _extract_level("SG-item-010") == "item"

    def test_extract_level_sg_without_type(self) -> None:
        """测试 SG 前缀但缺少 type 部分返回 unknown。"""
        assert _extract_level("SG") == "unknown"


class TestDifficultyLevelReport:
    """按难度分层统计报告测试。"""

    def test_report_md_contains_difficulty_section(self) -> None:
        """测试 Markdown 报告包含按难度分层统计章节。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="测试1", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={"tool_selection": 5, "param_quality": 5, "call_efficiency": 5, "result_utilization": 5},
                    total_score=5.0,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        assert "## 按难度分层统计" in content
        assert "| 难度 | 用例数 | 平均总分 | 通过率 |" in content
        assert "| Level 1 | 1 | 5.00 | 100% |" in content

    def test_report_md_multiple_levels(self) -> None:
        """测试多难度等级分层统计。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="L1问题", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={"tool_selection": 5, "param_quality": 5, "call_efficiency": 5, "result_utilization": 5},
                    total_score=5.0,
                    commentary="",
                ),
            ),
            (
                CaseResult(case_id="L1-002", question="L1问题2", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-002",
                    scores={"tool_selection": 3, "param_quality": 3, "call_efficiency": 3, "result_utilization": 3},
                    total_score=3.0,
                    commentary="",
                ),
            ),
            (
                CaseResult(case_id="L2-001", question="L2问题", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L2-001",
                    scores={"tool_selection": 1, "param_quality": 1, "call_efficiency": 1, "result_utilization": 1},
                    total_score=1.0,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        assert "## 按难度分层统计" in content
        assert "| Level 1 | 2 | 4.00 | 100% |" in content
        assert "| Level 2 | 1 | 1.00 | 0% |" in content

    def test_report_md_empty_results_difficulty_section(self) -> None:
        """测试空结果时按难度分层统计显示无数据。"""
        report_data = _build_json([])
        content = _build_md([], report_data)

        assert "## 按难度分层统计" in content
        assert "无数据" in content

    def test_report_md_difficulty_section_before_typical_cases(self) -> None:
        """测试按难度分层统计在典型案例章节之前。"""
        results = [
            (
                CaseResult(case_id="L1-001", question="测试", tool_calls=[], final_answer="回答", execution_time=1.0),
                EvalScore(
                    case_id="L1-001",
                    scores={"tool_selection": 4, "param_quality": 4, "call_efficiency": 4, "result_utilization": 4},
                    total_score=4.0,
                    commentary="",
                ),
            ),
        ]

        report_data = _build_json(results)
        content = _build_md(results, report_data)

        difficulty_pos = content.index("## 按难度分层统计")
        typical_pos = content.index("## 典型案例")
        # 按难度分层统计应在典型案例之前
        assert difficulty_pos < typical_pos
