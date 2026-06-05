"""BaseTask 模式钩子和 dimensions 加载测试。"""

from unittest.mock import MagicMock
from pathlib import Path

import pytest

from novel_eval.models import EvalCase, CaseResult


def test_dimensions_from_config() -> None:
    """dimensions 从 config.tasks[name].dimensions 加载。"""
    from novel_eval.tasks.base import BaseTask

    config = {
        "tasks": {
            "test_task": {
                "dimensions": [
                    {"key": "dim1", "label": "维度1", "description": "测试维度1"},
                    {"key": "dim2", "label": "维度2", "description": "测试维度2"},
                ]
            }
        }
    }

    # 创建一个具体的子类来测试
    class ConcreteTask(BaseTask):
        def generate_cases(self, book, settings_dir, **kwargs): return []
        async def run_case(self, case, runner=None, **kwargs): return None
        async def judge_case(self, case_result, judge_llm=None, **kwargs): return None

    task = ConcreteTask(name="test_task", config=config)
    assert len(task.dimensions) == 2
    assert task.dimensions[0]["key"] == "dim1"
    assert task.dimensions[1]["key"] == "dim2"


def test_dimensions_empty_fallback() -> None:
    """config 无 dimensions 时返回空列表。"""
    from novel_eval.tasks.base import BaseTask

    class ConcreteTask(BaseTask):
        def generate_cases(self, book, settings_dir, **kwargs): return []
        async def run_case(self, case, runner=None, **kwargs): return None
        async def judge_case(self, case_result, judge_llm=None, **kwargs): return None

    task = ConcreteTask(name="test_task", config={})
    assert task.dimensions == []


def test_load_existing_results_default(tmp_path: Path) -> None:
    """默认实现：从 result_dir/cases/*.json 加载。"""
    from novel_eval.tasks.base import BaseTask

    class ConcreteTask(BaseTask):
        def generate_cases(self, book, settings_dir, **kwargs): return []
        async def run_case(self, case, runner=None, **kwargs): return None
        async def judge_case(self, case_result, judge_llm=None, **kwargs): return None

    task = ConcreteTask(name="test_task")

    # 创建 cases 目录和一个 mock case JSON
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    import json
    case_data = {
        "case_id": "L1-001",
        "question": "测试",
        "tool_calls": [],
        "final_answer": "回答",
        "execution_time": 1.0,
        "score": {"scores": {"tool_selection": 5}, "total_score": 5.0, "commentary": ""},
    }
    (cases_dir / "L1-001.json").write_text(json.dumps(case_data), encoding="utf-8")

    results = task.load_existing_results([], tmp_path)
    assert len(results) == 1
    assert results[0].case_id == "L1-001"


def test_detect_missing_default() -> None:
    """默认实现返回空列表。"""
    from novel_eval.tasks.base import BaseTask

    class ConcreteTask(BaseTask):
        def generate_cases(self, book, settings_dir, **kwargs): return []
        async def run_case(self, case, runner=None, **kwargs): return None
        async def judge_case(self, case_result, judge_llm=None, **kwargs): return None

    task = ConcreteTask(name="test_task")
    assert task.detect_missing([]) == []


def test_supports_rerun_missing_default() -> None:
    """默认 supports_rerun_missing 为 False。"""
    from novel_eval.tasks.base import BaseTask

    class ConcreteTask(BaseTask):
        def generate_cases(self, book, settings_dir, **kwargs): return []
        async def run_case(self, case, runner=None, **kwargs): return None
        async def judge_case(self, case_result, judge_llm=None, **kwargs): return None

    task = ConcreteTask(name="test_task")
    assert task.supports_rerun_missing is False


def test_tool_usage_supports_rerun_missing() -> None:
    """ToolUsageTask 的 supports_rerun_missing 为 True。"""
    from novel_eval.tasks.tool_usage import ToolUsageTask
    from novel_eval.tasks.tool_usage.models import EvalConfig

    task = ToolUsageTask(EvalConfig())
    assert task.supports_rerun_missing is True


def test_tool_usage_dimensions_from_config() -> None:
    """ToolUsageTask 从 config 加载 dimensions。"""
    from novel_eval.tasks.tool_usage import ToolUsageTask
    from novel_eval.tasks.tool_usage.models import EvalConfig

    config = EvalConfig()
    task = ToolUsageTask(config)
    # ToolUsageTask 初始化时 config 是 EvalConfig.model_dump()，不含 dimensions
    # 所以 dimensions 应为空列表（因为没有从 eval_config.yaml 加载）
    assert isinstance(task.dimensions, list)
