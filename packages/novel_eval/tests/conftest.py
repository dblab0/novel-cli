"""Eval Viewer 测试 fixture 和数据工厂。

提供：
- make_case_json / make_report_json / make_messages_md: 构造测试数据
- setup_eval_dir: 构造模拟 eval_results 目录结构
- mock_eval_dir / mock_index / client: pytest fixture
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ------------------------------------------------------------------
# 数据工厂函数
# ------------------------------------------------------------------


def make_case_json(
    case_id: str = "L1-001",
    question: str = "测试问题",
    total_score: float = 4.0,
    tool_calls: list[dict] | None = None,
    execution_time: float = 3.5,
    final_answer: str = "测试回答",
) -> dict:
    """构造标准 case JSON 数据。

    Args:
        case_id: 用例 ID。
        question: 问题文本。
        total_score: 总评分，用于自动生成各维度分数。
        tool_calls: 工具调用列表，默认包含一条 SearchEntity 调用。
        execution_time: 执行耗时（秒）。
        final_answer: 最终回答文本。

    Returns:
        可序列化为 JSON 的用例字典。
    """
    if tool_calls is None:
        tool_calls = [
            {
                "step": 1,
                "tool_name": "SearchEntity",
                "params": {"entity": "韩立"},
                "result_summary": "找到韩立实体",
            },
        ]

    # 根据 total_score 生成各维度评分（取整）
    avg = int(total_score)
    scores = {
        "tool_selection": avg,
        "param_quality": avg,
        "call_efficiency": avg,
        "result_utilization": avg,
    }

    return {
        "case_id": case_id,
        "question": question,
        "tool_calls": tool_calls,
        "final_answer": final_answer,
        "execution_time": execution_time,
        "score": {
            "scores": scores,
            "total_score": total_score,
            "commentary": "测试评语",
        },
    }


def make_report_json(
    total_cases: int = 2,
    total_score: float = 3.5,
    pass_rate: float = 0.5,
    dimension_scores: dict[str, float] | None = None,
    cases_summary: list[dict] | None = None,
    task_type: str | None = "tool_usage",
    dimensions: list[dict] | None = None,
) -> dict:
    """构造标准 report.json 数据。

    Args:
        total_cases: 总用例数。
        total_score: 平均总分。
        pass_rate: 通过率。
        dimension_scores: 各维度评分，默认为四维度标准值。
        cases_summary: 用例摘要列表，会截断到 total_cases 长度。
        task_type: 任务类型，默认 "tool_usage"。
        dimensions: 维度配置列表，默认为 tool_usage 四维度。

    Returns:
        可序列化为 JSON 的报告字典。
    """
    if dimension_scores is None:
        dimension_scores = {
            "tool_selection": 4.0,
            "param_quality": 3.5,
            "call_efficiency": 3.0,
            "result_utilization": 3.5,
        }

    if cases_summary is None:
        cases_summary = [
            {
                "case_id": "L1-001",
                "question": "问题1",
                "total_score": 4.0,
                "execution_time": 3.5,
            },
            {
                "case_id": "L1-002",
                "question": "问题2",
                "total_score": 3.0,
                "execution_time": 2.5,
            },
        ][:total_cases]

    result: dict[str, Any] = {
        "total_cases": total_cases,
        "total_score": total_score,
        "pass_rate": pass_rate,
        "dimension_scores": dimension_scores,
        "cases_summary": cases_summary,
    }

    # task_type 和 dimensions 字段
    if task_type is not None:
        result["task_type"] = task_type
    if dimensions is not None:
        result["dimensions"] = dimensions

    return result


def make_messages_md(case_id: str = "L1-001") -> str:
    """构造标准 messages.md 内容。

    Args:
        case_id: 用例 ID，用于在标题中体现。

    Returns:
        Markdown 格式的对话记录文本。
    """
    return (
        f"# Case {case_id} 对话记录\n\n"
        f"## 用户\n韩立是谁？\n\n"
        f"## 助手\n韩立是《凡人修仙传》的主角。\n"
    )


def setup_eval_dir(
    base_dir: Path,
    book: str = "凡人修仙传",
    agent: str = "novel-v2",
    scenario: str = "level_1_entity",
    run_id: str = "minimax-m2.7_2026-04-29_135831",
    report: dict | None = None,
    cases: list[dict] | None = None,
    include_messages: bool = True,
) -> Path:
    """构造模拟的 eval_results 目录结构。

    Args:
        base_dir: 基础临时目录。
        book: 书籍名称。
        agent: Agent 名称。
        scenario: 场景名称。
        run_id: 运行标识（格式需为 {model}_{timestamp}）。
        report: report.json 数据，默认使用 make_report_json()。
        cases: 用例数据列表，默认包含一条 make_case_json()。
        include_messages: 是否为每个用例生成 messages.md。

    Returns:
        eval_results 目录路径。
    """
    eval_dir = base_dir / "eval_results"
    run_dir = eval_dir / book / agent / scenario / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 写入 report.json
    if report is None:
        report = make_report_json()
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )

    # 写入 cases/
    cases_dir = run_dir / "cases"
    cases_dir.mkdir()

    if cases is None:
        cases = [make_case_json()]

    for case in cases:
        case_id = case.get("case_id", "L1-001")
        (cases_dir / f"{case_id}.json").write_text(
            json.dumps(case, ensure_ascii=False), encoding="utf-8"
        )
        if include_messages:
            case_msg_dir = cases_dir / case_id
            case_msg_dir.mkdir()
            (case_msg_dir / "messages.md").write_text(
                make_messages_md(case_id), encoding="utf-8"
            )

    return eval_dir


# ------------------------------------------------------------------
# pytest fixture
# ------------------------------------------------------------------


@pytest.fixture
def mock_eval_dir(tmp_path):
    """构造模拟的 eval_results 目录结构。

    结构：
        eval_results/
        └── 凡人修仙传/
            ├── novel-v2/
            │   └── level_1_entity/
            │       └── minimax-m2.7_2026-04-29_135831/
            │           ├── report.json
            │           └── cases/
            │               ├── L1-001.json
            │               └── L1-001/
            │                   └── messages.md
            └── novel-v3/
                └── level_1_entity/
                    └── minimax-m2.7_2026-04-30_115625/
                        ├── report.json
                        └── cases/
                            ├── L1-001.json
                            └── L1-002.json

    Returns:
        eval_results 目录路径（Path 对象）。
    """
    eval_dir = tmp_path / "eval_results"

    # novel-v2 run
    v2_run = (
        eval_dir
        / "凡人修仙传"
        / "novel-v2"
        / "level_1_entity"
        / "minimax-m2.7_2026-04-29_135831"
    )
    v2_run.mkdir(parents=True)
    (v2_run / "report.json").write_text(
        json.dumps(
            make_report_json(total_cases=1, total_score=3.5, pass_rate=1.0),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    v2_cases = v2_run / "cases"
    v2_cases.mkdir()
    (v2_cases / "L1-001.json").write_text(
        json.dumps(make_case_json("L1-001", total_score=3.5), ensure_ascii=False),
        encoding="utf-8",
    )
    v2_msg = v2_cases / "L1-001"
    v2_msg.mkdir()
    (v2_msg / "messages.md").write_text(
        make_messages_md("L1-001"), encoding="utf-8"
    )

    # novel-v3 run
    v3_run = (
        eval_dir
        / "凡人修仙传"
        / "novel-v3"
        / "level_1_entity"
        / "minimax-m2.7_2026-04-30_115625"
    )
    v3_run.mkdir(parents=True)
    (v3_run / "report.json").write_text(
        json.dumps(
            make_report_json(total_cases=2, total_score=4.0, pass_rate=1.0),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    v3_cases = v3_run / "cases"
    v3_cases.mkdir()
    (v3_cases / "L1-001.json").write_text(
        json.dumps(make_case_json("L1-001", total_score=4.0), ensure_ascii=False),
        encoding="utf-8",
    )
    (v3_cases / "L1-002.json").write_text(
        json.dumps(make_case_json("L1-002", total_score=4.0), ensure_ascii=False),
        encoding="utf-8",
    )

    return eval_dir


@pytest.fixture
def mock_index(mock_eval_dir):
    """基于 mock_eval_dir 构建的 EvalDataIndex 实例。

    Returns:
        已完成索引构建的 EvalDataIndex。
    """
    from novel_eval.viewer.data import EvalDataIndex

    return EvalDataIndex(mock_eval_dir)


@pytest.fixture
def client(mock_eval_dir):
    """FastAPI TestClient，注入 mock_eval_dir 目录。

    create_app 会内部创建新的 EvalDataIndex(eval_dir)，
    由于传入同一个 eval_dir，索引数据与 mock_index 一致。

    Returns:
        FastAPI TestClient 实例。
    """
    from novel_eval.viewer.server import create_app

    app = create_app(mock_eval_dir, dev=True)
    return TestClient(app)
