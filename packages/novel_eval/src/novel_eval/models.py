"""
通用数据模型模块。

定义评估过程中使用的核心数据结构。

主要模型：
- EvalCase: 评估用例
- ToolCallRecord: 工具调用记录
- CaseResult: 用例执行结果
- EvalScore: 评估评分

这些模型用于：
1. 从 YAML 用例文件加载测试用例
2. 从 wire.jsonl 解析工具调用记录
3. 生成评估报告
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalCase:
    """评估用例。

    表示单个测试用例，包含问题及其元数据。

    Attributes:
        id: 用例唯一标识，如 "L1-001"。
        book: 书籍名称，如 "凡人修仙传"。
        question: 用户问题。
        meta: 元数据，包含 involved_entities 和 expected_tool_types。
        validation: 预跑验证结果（可选）。
    """

    id: str
    book: str
    question: str
    meta: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] | None = None


@dataclass
class ToolCallRecord:
    """工具调用记录。

    记录单次工具调用的详细信息。

    Attributes:
        step: 调用步骤序号（第几步调用）。
        tool_name: 工具名称（如 SearchEntity / SearchGraph / SearchCorpus）。
        params: 实际传入的参数字典。
        result_summary: 工具返回结果摘要（截断到合理长度）。
    """

    step: int
    tool_name: str
    params: dict[str, Any]
    result_summary: str


@dataclass
class CaseResult:
    """用例执行结果。

    记录单个用例的完整执行结果。

    Attributes:
        case_id: 用例 ID。
        question: 用户问题。
        tool_calls: 完整工具调用链。
        final_answer: agent 最终回答。
        execution_time: 执行耗时（秒）。
        error: 错误信息（如果有）。
    """

    case_id: str
    question: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_answer: str = ""
    execution_time: float = 0.0
    error: str | None = None


@dataclass
class EvalScore:
    """评估评分。

    记录单个用例的评估分数和评语。

    Attributes:
        case_id: 用例 ID。
        scores: 各维度分数字典，键为维度名称，值为分数（1-5）。
        total_score: 加权平均总分。
        commentary: 具体评价和扣分原因。
    """

    case_id: str
    scores: dict[str, int] = field(default_factory=dict)
    total_score: float = 0.0
    commentary: str = ""


def load_scored_results_from_dir(result_dir: Path) -> list[tuple[CaseResult, EvalScore]]:
    """从评估结果目录加载 (CaseResult, EvalScore) 列表。

    用于 rerun-missing 模式，从已有的评估结果中同时加载执行结果和评分，
    以便与新生成的结果合并。

    Args:
        result_dir: 评估结果目录，包含 cases/*.json 文件。

    Returns:
        (CaseResult, EvalScore) 元组列表。

    Raises:
        FileNotFoundError: 目录不存在或 cases 目录不存在。
    """
    cases_dir = result_dir / "cases"
    if not cases_dir.exists():
        raise FileNotFoundError(f"cases 目录不存在: {cases_dir}")

    results: list[tuple[CaseResult, EvalScore]] = []
    for case_file in sorted(cases_dir.glob("*.json")):
        with open(case_file, encoding="utf-8") as f:
            data = json.load(f)

        # 解析 tool_calls（兼容旧字段 action）
        tool_calls = [
            ToolCallRecord(
                step=tc["step"],
                tool_name=tc["tool_name"],
                params=tc["params"],
                result_summary=tc["result_summary"],
            )
            for tc in data.get("tool_calls", [])
        ]

        case_result = CaseResult(
            case_id=data["case_id"],
            question=data["question"],
            tool_calls=tool_calls,
            final_answer=data.get("final_answer", ""),
            execution_time=data.get("execution_time", 0.0),
            error=data.get("error"),
        )

        # 解析 score 字段
        score_data = data.get("score", {})
        eval_score = EvalScore(
            case_id=data["case_id"],
            scores=score_data.get("scores", {}),
            total_score=score_data.get("total_score", 0.0),
            commentary=score_data.get("commentary", ""),
        )

        results.append((case_result, eval_score))

    return results


def load_case_results_from_dir(result_dir: Path) -> list[CaseResult]:
    """从评估结果目录加载 CaseResult 列表。

    用于 skip-run 模式，从已有的评估结果中加载执行结果，
    然后仅进行 Judge 评分。

    Args:
        result_dir: 评估结果目录，包含 cases/*.json 文件。

    Returns:
        CaseResult 列表。

    Raises:
        FileNotFoundError: 目录不存在或 cases 目录不存在。
    """
    cases_dir = result_dir / "cases"
    if not cases_dir.exists():
        raise FileNotFoundError(f"cases 目录不存在: {cases_dir}")

    results: list[CaseResult] = []
    for case_file in sorted(cases_dir.glob("*.json")):
        with open(case_file, encoding="utf-8") as f:
            data = json.load(f)

        # 解析 tool_calls（兼容旧字段 action）
        tool_calls = [
            ToolCallRecord(
                step=tc["step"],
                tool_name=tc["tool_name"],
                params=tc["params"],
                result_summary=tc["result_summary"],
            )
            for tc in data.get("tool_calls", [])
        ]

        results.append(CaseResult(
            case_id=data["case_id"],
            question=data["question"],
            tool_calls=tool_calls,
            final_answer=data.get("final_answer", ""),
            execution_time=data.get("execution_time", 0.0),
            error=data.get("error"),
        ))

    return results