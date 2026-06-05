"""Judge 评分单测。

测试 judge 模块中的 judge_case 和辅助函数。
"""

import pytest

from novel_eval.models import CaseResult, EvalScore, ToolCallRecord
from novel_eval.tasks.tool_usage.judge import (
    _format_tool_calls,
    _parse_judge_response,
    judge_case,
)


class TestJudgeCase:
    """测试 judge_case 函数。"""

    def test_judge_case_empty_tool_calls(self) -> None:
        """测试无工具调用时的评分（边界情况）。"""
        case_result = CaseResult(
            case_id="L1-001",
            question="韩立是谁？",
            tool_calls=[],
            final_answer="韩立是男主角。",
        )
        judge_config = {}

        score = judge_case(case_result, judge_config)

        assert score.case_id == "L1-001"
        assert score.scores == {
            "tool_selection": 1,
            "param_quality": 1,
            "call_efficiency": 1,
            "result_utilization": 1,
        }
        assert score.total_score == 1.0
        assert score.commentary == "未使用工具"

    def test_judge_case_with_tool_calls_returns_mock_score(self) -> None:
        """测试有工具调用但 Judge LLM 配置缺失时返回默认分数。"""
        case_result = CaseResult(
            case_id="L1-001",
            question="韩立是谁？",
            tool_calls=[
                ToolCallRecord(
                    step=1,
                    tool_name="SearchEntity",
                    params={"name": "韩立"},
                    result_summary="韩立，男主角，元婴期修士...",
                ),
            ],
            final_answer="韩立是男主角，元婴期修士。",
        )
        judge_config = {}

        score = judge_case(case_result, judge_config)

        # Judge LLM 配置缺失时返回默认分数
        assert score.case_id == "L1-001"
        assert score.scores["tool_selection"] == 3
        assert score.scores["param_quality"] == 3
        assert score.scores["call_efficiency"] == 3
        assert score.scores["result_utilization"] == 3
        assert "调用失败" in score.commentary

    def test_judge_case_multiple_tool_calls(self) -> None:
        """测试多个工具调用的情况。"""
        case_result = CaseResult(
            case_id="L2-001",
            question="韩立和南宫婉的关系？",
            tool_calls=[
                ToolCallRecord(
                    step=1,
                    tool_name="SearchEntity",
                    params={"name": "韩立"},
                    result_summary="韩立，男主角...",
                ),
                ToolCallRecord(
                    step=2,
                    tool_name="SearchEntity",
                    params={"name": "南宫婉"},
                    result_summary="南宫婉，女主角...",
                ),
                ToolCallRecord(
                    step=3,
                    tool_name="SearchGraph",
                    params={"from": "韩立", "to": "南宫婉", "relation": "夫妻"},
                    result_summary="二人为夫妻关系...",
                ),
            ],
            final_answer="韩立和南宫婉是夫妻关系。",
        )
        judge_config = {}

        score = judge_case(case_result, judge_config)

        assert score.case_id == "L2-001"


class TestFormatToolCalls:
    """测试 _format_tool_calls 函数。"""

    def test_format_single_tool_call(self) -> None:
        """测试格式化单个工具调用。"""
        tool_calls = [
            ToolCallRecord(
                step=1,
                tool_name="SearchEntity",
                params={"name": "韩立"},
                result_summary="韩立，男主角，元婴期修士...",
            ),
        ]

        result = _format_tool_calls(tool_calls)

        assert "步骤 1:" in result
        assert "tool=SearchEntity" in result
        assert "params={'name': '韩立'}" in result
        assert "结果摘要:" in result

    def test_format_multiple_tool_calls(self) -> None:
        """测试格式化多个工具调用。"""
        tool_calls = [
            ToolCallRecord(
                step=1,
                tool_name="SearchEntity",
                params={"name": "韩立"},
                result_summary="韩立信息...",
            ),
            ToolCallRecord(
                step=2,
                tool_name="SearchGraph",
                params={"from": "韩立", "to": "南宫婉"},
                result_summary="关系信息...",
            ),
        ]

        result = _format_tool_calls(tool_calls)

        assert "步骤 1:" in result
        assert "步骤 2:" in result
        assert "tool=SearchEntity" in result
        assert "tool=SearchGraph" in result

    def test_format_preserves_full_summary(self) -> None:
        """测试完整保留结果摘要，不做截断。"""
        long_summary = "这是一个非常长的结果摘要" * 20  # 约 400 字符
        tool_calls = [
            ToolCallRecord(
                step=1,
                tool_name="SearchEntity",
                params={"name": "韩立"},
                result_summary=long_summary,
            ),
        ]

        result = _format_tool_calls(tool_calls)

        # 结果摘要应完整保留
        assert long_summary in result

    def test_format_empty_result_summary(self) -> None:
        """测试空结果摘要不显示摘要行。"""
        tool_calls = [
            ToolCallRecord(
                step=1,
                tool_name="SearchEntity",
                params={"name": "韩立"},
                result_summary="",  # 空摘要
            ),
        ]

        result = _format_tool_calls(tool_calls)

        assert "步骤 1:" in result
        assert "结果摘要:" not in result


class TestParseJudgeResponse:
    """测试 _parse_judge_response 函数（JSON 优先多层解析）。"""

    def test_parse_valid_json(self) -> None:
        """测试直接解析有效 JSON 响应。"""
        response = '{"tool_selection": 4, "param_quality": 3, "call_efficiency": 5, "result_utilization": 4, "commentary": "工具选择准确"}'

        scores, commentary = _parse_judge_response(response)

        assert scores["tool_selection"] == 4
        assert scores["param_quality"] == 3
        assert scores["call_efficiency"] == 5
        assert scores["result_utilization"] == 4
        assert commentary == "工具选择准确"

    def test_parse_score_out_of_range(self) -> None:
        """测试分数超出范围时被修正到 1-5。"""
        response = '{"tool_selection": 10, "param_quality": 0, "call_efficiency": 3, "result_utilization": 3, "commentary": "测试"}'

        scores, _ = _parse_judge_response(response)

        assert scores["tool_selection"] == 5  # 10 -> 5
        assert scores["param_quality"] == 1  # 0 -> 1

    def test_parse_empty_response_returns_defaults(self) -> None:
        """测试空响应返回默认值。"""
        scores, commentary = _parse_judge_response("")

        assert scores == {
            "tool_selection": 3,
            "param_quality": 3,
            "call_efficiency": 3,
            "result_utilization": 3,
        }
        assert "无法解析评分" in commentary

    def test_parse_json_code_block(self) -> None:
        """测试从 ```json``` 代码块中提取 JSON。"""
        response = '```json\n{"tool_selection": 4, "param_quality": 3, "call_efficiency": 5, "result_utilization": 4, "commentary": "评语"}\n```'

        scores, _ = _parse_judge_response(response)

        assert scores["tool_selection"] == 4
