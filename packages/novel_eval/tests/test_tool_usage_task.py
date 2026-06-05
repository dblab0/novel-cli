"""ToolUsageTask 集成测试。

测试 ToolUsageTask 的完整流程，包括初始化、用例加载、执行和评分。
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_eval.models import CaseResult, EvalCase, EvalScore, ToolCallRecord
from novel_eval.tasks.tool_usage import ToolUsageTask
from novel_eval.tasks.tool_usage.models import EvalConfig


class TestToolUsageTask:
    """ToolUsageTask 集成测试。"""

    def test_task_initialization(self) -> None:
        """测试任务初始化。"""
        config = EvalConfig()
        task = ToolUsageTask(config)

        assert task.name == "tool_usage"
        assert task.eval_config == config
        assert task.runner is not None
        # 默认配置无 API Key，judge 应为 None
        assert task.judge is None

    def test_task_initialization_with_api_key(self) -> None:
        """测试带 API Key 的初始化会创建 Judge 实例。"""
        config = EvalConfig()
        config.judge.api_key = "test-api-key"
        task = ToolUsageTask(config)

        assert task.judge is not None

    def test_task_initialization_default_config(self) -> None:
        """测试无配置时使用默认配置。"""
        task = ToolUsageTask()

        assert task.eval_config is not None
        assert task.runner is not None

    def test_load_cases_from_yaml(self, tmp_path: Path) -> None:
        """测试从 YAML 加载用例。"""
        # 创建测试 YAML
        yaml_content = """
book: 凡人修仙传
level: 1
description: Level 1 测试用例
cases:
  - id: L1-001
    model: deepseek-v3
    question: 韩立是谁？
    meta:
      involved_entities: ["韩立"]
      expected_tool_types: ["entity"]
"""
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml_content, encoding="utf-8")

        config = EvalConfig()
        task = ToolUsageTask(config)
        cases = task.load_cases(yaml_path)

        assert len(cases) == 1
        assert cases[0].id == "L1-001"
        assert cases[0].question == "韩立是谁？"
        assert cases[0].book == "凡人修仙传"

    def test_generate_cases_returns_empty(self, tmp_path: Path) -> None:
        """测试生成用例当前返回空列表（框架实现）。"""
        config = EvalConfig()
        task = ToolUsageTask(config)
        cases = task.generate_cases("凡人修仙传", tmp_path)

        # 当前为框架实现，返回空列表
        assert cases == []

    async def test_run_case_with_mock_runner(self) -> None:
        """测试执行单个用例（使用 Mock Runner）。"""
        config = EvalConfig()
        task = ToolUsageTask(config)

        # 创建测试用例
        case = EvalCase(
            id="L1-001",
            book="凡人修仙传",
            question="韩立是谁？",
        )

        # Mock run_case 结果
        mock_result = CaseResult(
            case_id="L1-001",
            question="韩立是谁？",
            tool_calls=[
                ToolCallRecord(
                    step=1,
                    tool_name="entity",
                    params={"query": "韩立"},
                    result_summary="韩立是男主角...",
                ),
            ],
            final_answer="韩立是男主角",
            execution_time=1.0,
        )

        with patch.object(task.runner, "run_case", return_value=mock_result):
            result = await task.run_case(case)

        assert result.case_id == "L1-001"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "entity"

    async def test_run_batch_with_mock_runner(self) -> None:
        """测试批量执行用例（使用 Mock Runner）。"""
        config = EvalConfig()
        task = ToolUsageTask(config)

        # 创建测试用例
        cases = [
            EvalCase(id="L1-001", book="凡人修仙传", question="韩立是谁？"),
            EvalCase(id="L1-002", book="凡人修仙传", question="南宫婉是谁？"),
        ]

        # Mock 结果
        mock_results = [
            CaseResult(
                case_id="L1-001",
                question="韩立是谁？",
                tool_calls=[
                    ToolCallRecord(
                        step=1,
                        tool_name="entity",
                        params={"query": "韩立"},
                        result_summary="韩立是男主角...",
                    ),
                ],
                final_answer="韩立是男主角",
                execution_time=1.0,
            ),
            CaseResult(
                case_id="L1-002",
                question="南宫婉是谁？",
                tool_calls=[
                    ToolCallRecord(
                        step=1,
                        tool_name="entity",
                        params={"query": "南宫婉"},
                        result_summary="南宫婉是女主角...",
                    ),
                ],
                final_answer="南宫婉是女主角",
                execution_time=1.0,
            ),
        ]

        with patch.object(task.runner, "run_batch", new_callable=AsyncMock, return_value=mock_results):
            results = await task.run_batch(cases)

        assert len(results) == 2
        assert results[0].case_id == "L1-001"
        assert results[1].case_id == "L1-002"

    async def test_judge_case_empty_tool_calls(self) -> None:
        """测试无工具调用时的评分。"""
        config = EvalConfig()
        task = ToolUsageTask(config)

        case_result = CaseResult(
            case_id="L1-001",
            question="韩立是谁？",
            tool_calls=[],  # 无工具调用
            final_answer="韩立是男主角",
            execution_time=1.0,
        )

        score = await task.judge_case(case_result)

        assert score.case_id == "L1-001"
        assert score.scores["tool_selection"] == 1
        assert score.scores["param_quality"] == 1
        assert score.scores["call_efficiency"] == 1
        assert score.scores["result_utilization"] == 1
        assert score.total_score == 1.0
        assert score.commentary == "未使用工具"

    async def test_judge_case_with_tool_calls(self) -> None:
        """测试有工具调用时的评分（返回模拟值）。"""
        config = EvalConfig()
        task = ToolUsageTask(config)

        case_result = CaseResult(
            case_id="L1-001",
            question="韩立是谁？",
            tool_calls=[
                ToolCallRecord(
                    step=1,
                    tool_name="entity",
                    params={"query": "韩立"},
                    result_summary="韩立是男主角...",
                ),
            ],
            final_answer="韩立是男主角",
            execution_time=1.0,
        )

        score = await task.judge_case(case_result)

        assert score.case_id == "L1-001"
        # Judge LLM 配置缺失时返回默认分数
        assert score.scores["tool_selection"] == 3
        assert score.scores["param_quality"] == 3
        assert "调用失败" in score.commentary

    async def test_evaluate_flow(self) -> None:
        """测试完整评估流程。"""
        config = EvalConfig()
        task = ToolUsageTask(config)

        # 创建测试用例
        cases = [
            EvalCase(id="L1-001", book="凡人修仙传", question="韩立是谁？"),
        ]

        # Mock 执行结果
        mock_result = CaseResult(
            case_id="L1-001",
            question="韩立是谁？",
            tool_calls=[
                ToolCallRecord(
                    step=1,
                    tool_name="entity",
                    params={"query": "韩立"},
                    result_summary="韩立是男主角...",
                ),
            ],
            final_answer="韩立是男主角",
            execution_time=1.0,
        )

        with patch.object(task.runner, "run_batch", new_callable=AsyncMock, return_value=[mock_result]):
            results = await task.evaluate(cases)

        assert len(results) == 1
        case_result, score = results[0]
        assert case_result.case_id == "L1-001"

    async def test_evaluate_single(self) -> None:
        """测试单用例评估。"""
        config = EvalConfig()
        task = ToolUsageTask(config)

        case = EvalCase(id="L1-001", book="凡人修仙传", question="韩立是谁？")

        # Mock 执行结果
        mock_result = CaseResult(
            case_id="L1-001",
            question="韩立是谁？",
            tool_calls=[
                ToolCallRecord(
                    step=1,
                    tool_name="entity",
                    params={"query": "韩立"},
                    result_summary="韩立是男主角...",
                ),
            ],
            final_answer="韩立是男主角",
            execution_time=1.0,
        )

        with patch.object(task.runner, "run_case", return_value=mock_result):
            result, score = await task.evaluate_single(case)

        assert result.case_id == "L1-001"
        assert score.case_id == "L1-001"

    def test_save_cases_to_yaml(self, tmp_path: Path) -> None:
        """测试保存用例到 YAML 文件。"""
        config = EvalConfig()
        task = ToolUsageTask(config)

        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="韩立是谁？",
                meta={"involved_entities": ["韩立"]},
            ),
        ]

        base_dir = tmp_path / "eval_cases"
        task.save_cases(base_dir, cases, "凡人修仙传", scenario="level_1_entity")

        # 验证文件存在
        yaml_file = base_dir / "凡人修仙传" / "level_1_entity.yaml"
        assert yaml_file.exists()

        # 验证可加载
        loaded_cases = task.load_cases(yaml_file)
        assert len(loaded_cases) == 1
        assert loaded_cases[0].id == "L1-001"


class TestToolUsageTaskConfigIntegration:
    """ToolUsageTask 与配置集成测试。"""

    def test_config_propagation_to_runner(self) -> None:
        """测试配置正确传递给 Runner。"""
        config = EvalConfig()
        config.runner.work_dir = "~/test_work_dir"
        config.runner.timeout = 120
        config.runner.agent_file = "agents/novel/agent.yaml"

        task = ToolUsageTask(config)

        assert task.runner.work_dir.name == "test_work_dir"
        assert task.runner.timeout == 120

    def test_concurrency_config(self) -> None:
        """测试并发配置。"""
        config = EvalConfig()
        config.concurrency = 5

        task = ToolUsageTask(config)

        # Runner 应使用配置的并发数
        assert task.runner.config.concurrency == 5