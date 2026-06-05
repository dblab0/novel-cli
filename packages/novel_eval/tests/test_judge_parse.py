"""Judge JSON 多层解析策略单测。

测试 _parse_judge_response 的 JSON 优先多层解析逻辑。
"""

import pytest

from novel_eval.tasks.tool_usage.judge import _parse_judge_response


class TestParseDirectJson:
    """测试层级 1：直接 JSON 解析。"""

    def test_parse_valid_json(self) -> None:
        """测试直接解析有效的 JSON 响应。"""
        response = '{"tool_selection": 4, "param_quality": 3, "call_efficiency": 5, "result_utilization": 4, "commentary": "工具选择准确"}'

        scores, commentary = _parse_judge_response(response)

        assert scores["tool_selection"] == 4
        assert scores["param_quality"] == 3
        assert scores["call_efficiency"] == 5
        assert scores["result_utilization"] == 4
        assert commentary == "工具选择准确"

    def test_parse_json_with_extra_whitespace(self) -> None:
        """测试解析带有多余空白的 JSON。"""
        response = """
        {
            "tool_selection": 5,
            "param_quality": 4,
            "call_efficiency": 3,
            "result_utilization": 5,
            "commentary": "评语内容"
        }
        """

        scores, commentary = _parse_judge_response(response)

        assert scores["tool_selection"] == 5
        assert commentary == "评语内容"


class TestParseCodeBlock:
    """测试层级 2：```json``` 代码块提取。"""

    def test_parse_json_code_block(self) -> None:
        """测试从 ```json``` 代码块中提取 JSON。"""
        response = '```json\n{"tool_selection": 4, "param_quality": 3, "call_efficiency": 5, "result_utilization": 4, "commentary": "评语"}\n```'

        scores, commentary = _parse_judge_response(response)

        assert scores["tool_selection"] == 4
        assert scores["param_quality"] == 3
        assert commentary == "评语"

    def test_parse_plain_code_block(self) -> None:
        """测试从 ``` ``` 代码块（无 json 标记）中提取 JSON。"""
        response = '```\n{"tool_selection": 3, "param_quality": 4, "call_efficiency": 3, "result_utilization": 3, "commentary": "一般"}\n```'

        scores, commentary = _parse_judge_response(response)

        assert scores["tool_selection"] == 3


class TestParseRegexExtract:
    """测试层级 3：正则提取 JSON 对象。"""

    def test_parse_json_embedded_in_text(self) -> None:
        """测试从夹杂文本中提取 JSON 对象。"""
        response = '根据分析，评分为：{"tool_selection": 4, "param_quality": 3, "call_efficiency": 5, "result_utilization": 4, "commentary": "还不错"} 以上是评分。'

        scores, commentary = _parse_judge_response(response)

        assert scores["tool_selection"] == 4
        assert commentary == "还不错"


class TestParseFallback:
    """测试层级 4：兜底默认分数。"""

    def test_parse_unparseable_text(self) -> None:
        """测试完全无法解析的文本返回默认 3 分。"""
        response = "这是一段完全无法解析的分析文本，没有任何结构化数据。"

        scores, commentary = _parse_judge_response(response)

        assert scores["tool_selection"] == 3
        assert scores["param_quality"] == 3
        assert scores["call_efficiency"] == 3
        assert scores["result_utilization"] == 3
        assert "无法解析评分" in commentary
        assert response in commentary

    def test_parse_empty_response(self) -> None:
        """测试空响应返回默认 3 分。"""
        scores, commentary = _parse_judge_response("")

        assert scores == {
            "tool_selection": 3,
            "param_quality": 3,
            "call_efficiency": 3,
            "result_utilization": 3,
        }
        assert "无法解析评分" in commentary


class TestScoreRangeConstraint:
    """测试分数范围约束（1-5）。"""

    def test_score_above_max(self) -> None:
        """测试分数超出上限被截断为 5。"""
        response = '{"tool_selection": 10, "param_quality": 3, "call_efficiency": 3, "result_utilization": 3, "commentary": "测试"}'

        scores, _ = _parse_judge_response(response)

        assert scores["tool_selection"] == 5

    def test_score_below_min(self) -> None:
        """测试分数低于下限被截断为 1。"""
        response = '{"tool_selection": 0, "param_quality": -1, "call_efficiency": 3, "result_utilization": 3, "commentary": "测试"}'

        scores, _ = _parse_judge_response(response)

        assert scores["tool_selection"] == 1
        assert scores["param_quality"] == 1

    def test_missing_dimension_defaults_to_3(self) -> None:
        """测试 JSON 中缺少某个维度时默认为 3 分。"""
        response = '{"tool_selection": 4, "param_quality": 5, "commentary": "部分维度缺失"}'

        scores, _ = _parse_judge_response(response)

        assert scores["tool_selection"] == 4
        assert scores["param_quality"] == 5
        assert scores["call_efficiency"] == 3  # 缺失维度默认 3
        assert scores["result_utilization"] == 3  # 缺失维度默认 3

    def test_commentary_non_string_converted(self) -> None:
        """测试 commentary 为非字符串时转为字符串。"""
        response = '{"tool_selection": 3, "param_quality": 3, "call_efficiency": 3, "result_utilization": 3, "commentary": 123}'

        scores, commentary = _parse_judge_response(response)

        assert isinstance(commentary, str)
        assert commentary == "123"
