"""Eval Viewer API 端点集成测试。

覆盖所有 REST API 端点：
1. /api/books - 书籍列表
2. /api/books/{book}/agents - Agent 列表
3. /api/books/{book}/agents/{agent}/scenarios - 场景列表
4. /api/books/{book}/agents/{agent}/scenarios/{scenario}/runs - 运行列表
5. /api/runs/{run_id} - 运行元数据
6. /api/runs/{run_id}/report - 报告详情
7. /api/runs/{run_id}/cases - 用例列表
8. /api/runs/{run_id}/cases/{case_id}/messages - 消息记录
9. /api/runs/{run_id}/cases/{case_id} - 用例详情
10. /api/compare - 多 Agent 对比
11. /api/trend - 时间趋势
12. /api/reindex - 索引重建
13. /healthz - 健康检查
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_case_json, make_report_json, setup_eval_dir


# ==================================================================
# 健康检查
# ==================================================================


class TestHealthAPI:
    """健康检查 API 测试。"""

    def test_healthz(self, client):
        """测试 /healthz 返回 ok 状态。"""
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ==================================================================
# 书籍 API
# ==================================================================


class TestBooksAPI:
    """书籍列表 API 测试。"""

    def test_list_books(self, client):
        """测试获取书籍列表。"""
        resp = client.get("/api/books")
        assert resp.status_code == 200
        books = resp.json()
        assert "凡人修仙传" in books

    def test_empty_books(self, tmp_path):
        """测试空目录时返回空列表。"""
        from novel_eval.viewer.server import create_app

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        app = create_app(empty_dir, dev=True)
        c = TestClient(app)

        resp = c.get("/api/books")
        assert resp.status_code == 200
        assert resp.json() == []


# ==================================================================
# Agent API
# ==================================================================


class TestAgentsAPI:
    """Agent 列表 API 测试。"""

    def test_list_agents(self, client):
        """测试获取指定书籍的 agent 列表。"""
        resp = client.get("/api/books/凡人修仙传/agents")
        assert resp.status_code == 200
        agents = resp.json()
        assert "novel-v2" in agents
        assert "novel-v3" in agents

    def test_book_not_found(self, client):
        """测试查询不存在的书籍返回 404。"""
        resp = client.get("/api/books/不存在的书/agents")
        assert resp.status_code == 404


# ==================================================================
# 场景 API
# ==================================================================


class TestScenariosAPI:
    """场景列表 API 测试。"""

    def test_list_scenarios(self, client):
        """测试获取指定书籍 + agent 的场景列表。"""
        resp = client.get("/api/books/凡人修仙传/agents/novel-v2/scenarios")
        assert resp.status_code == 200
        scenarios = resp.json()
        assert "level_1_entity" in scenarios

    def test_agent_not_found(self, client):
        """测试查询不存在的 agent 返回 404。"""
        resp = client.get("/api/books/凡人修仙传/agents/nonexistent/scenarios")
        assert resp.status_code == 404


# ==================================================================
# 运行 API
# ==================================================================


class TestRunsAPI:
    """运行批次 API 测试。"""

    def test_list_runs(self, client):
        """测试获取运行批次列表。"""
        resp = client.get(
            "/api/books/凡人修仙传/agents/novel-v2/scenarios/level_1_entity/runs"
        )
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "minimax-m2.7_2026-04-29_135831"

    def test_scenario_not_found(self, client):
        """测试查询不存在的 scenario 返回 404。"""
        resp = client.get(
            "/api/books/凡人修仙传/agents/novel-v2/scenarios/nonexistent/runs"
        )
        assert resp.status_code == 404


# ==================================================================
# 报告 API
# ==================================================================


class TestReportAPI:
    """报告详情 API 测试。"""

    def test_get_report(self, client):
        """测试获取指定运行的报告数据。"""
        resp = client.get("/api/runs/minimax-m2.7_2026-04-29_135831/report")
        assert resp.status_code == 200
        report = resp.json()
        assert report["total_score"] == 3.5
        assert report["total_cases"] == 1

    def test_get_report_v3(self, client):
        """测试获取 novel-v3 的报告数据。"""
        resp = client.get("/api/runs/minimax-m2.7_2026-04-30_115625/report")
        assert resp.status_code == 200
        report = resp.json()
        assert report["total_score"] == 4.0
        assert report["total_cases"] == 2

    def test_run_not_found(self, client):
        """测试查询不存在的运行返回 404。"""
        resp = client.get("/api/runs/nonexistent_run/report")
        assert resp.status_code == 404

    def test_get_run_info(self, client):
        """测试获取运行元数据。"""
        resp = client.get("/api/runs/minimax-m2.7_2026-04-29_135831")
        assert resp.status_code == 200
        info = resp.json()
        assert info["book"] == "凡人修仙传"
        assert info["agent"] == "novel-v2"
        assert info["scenario"] == "level_1_entity"
        assert info["model"] == "minimax-m2.7"
        assert info["timestamp"] == "2026-04-29_135831"

    def test_get_run_info_not_found(self, client):
        """测试查询不存在的运行元数据返回 404。"""
        resp = client.get("/api/runs/nonexistent_2026-01-01_000000")
        assert resp.status_code == 404


# ==================================================================
# 用例 API
# ==================================================================


class TestCasesAPI:
    """用例列表和详情 API 测试。"""

    def test_list_cases(self, client):
        """测试获取指定运行的用例列表。"""
        resp = client.get("/api/runs/minimax-m2.7_2026-04-30_115625/cases")
        assert resp.status_code == 200
        cases = resp.json()
        assert len(cases) == 2
        case_ids = [c["case_id"] for c in cases]
        assert "L1-001" in case_ids
        assert "L1-002" in case_ids

    def test_get_case_detail(self, client):
        """测试获取单个用例的完整数据。"""
        resp = client.get("/api/runs/minimax-m2.7_2026-04-30_115625/cases/L1-001")
        assert resp.status_code == 200
        case = resp.json()
        assert case["case_id"] == "L1-001"
        assert case["question"] == "测试问题"
        assert "score" in case
        assert "tool_calls" in case

    def test_case_not_found(self, client):
        """测试查询不存在的用例返回 404。"""
        resp = client.get("/api/runs/minimax-m2.7_2026-04-30_115625/cases/NONEXISTENT-999")
        assert resp.status_code == 404

    def test_get_case_messages(self, tmp_path):
        """测试获取用例的消息记录。

        通过数据层直接测试 get_case_messages 逻辑，
        验证数据层的 messages 读取逻辑正确。
        """
        from novel_eval.viewer.data import EvalDataIndex

        eval_dir = setup_eval_dir(tmp_path, include_messages=True)
        index = EvalDataIndex(eval_dir)

        messages = index.get_case_messages("minimax-m2.7_2026-04-29_135831", "L1-001")
        assert messages is not None
        assert "韩立" in messages

    def test_case_messages_not_found(self, client):
        """测试获取无消息记录的用例返回 404。"""
        # novel-v3 的 L1-002 无 messages.md，应返回 404
        resp = client.get("/api/runs/minimax-m2.7_2026-04-30_115625/cases/L1-002/messages")
        assert resp.status_code == 404

    def test_case_run_not_found(self, client):
        """测试查询不存在的运行下的用例列表返回 404。"""
        resp = client.get("/api/runs/nonexistent_run/cases")
        assert resp.status_code == 404

    def test_case_list_summary_fields(self, client):
        """测试用例摘要列表包含必要字段。"""
        resp = client.get("/api/runs/minimax-m2.7_2026-04-29_135831/cases")
        assert resp.status_code == 200
        cases = resp.json()
        assert len(cases) == 1
        case = cases[0]
        assert "case_id" in case
        assert "question" in case
        assert "execution_time" in case
        assert "total_score" in case
        assert "passed" in case
        assert "tool_calls_count" in case


# ==================================================================
# 对比 API
# ==================================================================


class TestCompareAPI:
    """多 Agent 对比 API 测试。"""

    def test_compare_multiple_agents(self, client):
        """测试对比多个 agent 的数据。"""
        resp = client.get(
            "/api/compare",
            params={
                "book": "凡人修仙传",
                "agents": "novel-v2,novel-v3",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "cases" in data
        assert "dimension_summary" in data

        summary = data["dimension_summary"]
        agents_in_summary = {s["agent"] for s in summary}
        assert "novel-v2" in agents_in_summary
        assert "novel-v3" in agents_in_summary

        v2_summary = next(s for s in summary if s["agent"] == "novel-v2")
        v3_summary = next(s for s in summary if s["agent"] == "novel-v3")
        assert v2_summary["total_score"] == 3.5
        assert v3_summary["total_score"] == 4.0

    def test_compare_single_agent(self, client):
        """测试对比单个 agent。"""
        resp = client.get(
            "/api/compare",
            params={
                "book": "凡人修仙传",
                "agents": "novel-v2",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["dimension_summary"]) == 1
        assert data["dimension_summary"][0]["agent"] == "novel-v2"

    def test_compare_missing_agent_data(self, client):
        """测试对比中包含无数据的 agent 返回零分汇总。"""
        resp = client.get(
            "/api/compare",
            params={
                "book": "凡人修仙传",
                "agents": "novel-v2,nonexistent",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        v2_summary = next(s for s in data["dimension_summary"] if s["agent"] == "novel-v2")
        assert v2_summary["total_score"] == 3.5

        missing_summary = next(s for s in data["dimension_summary"] if s["agent"] == "nonexistent")
        assert missing_summary["total_score"] == 0.0

    def test_compare_includes_run_ids(self, client):
        """测试对比 API 返回每个 case 行包含 run_ids 字段。"""
        resp = client.get("/api/compare?book=凡人修仙传&agents=novel-v2,novel-v3&scenario=level_1_entity")
        assert resp.status_code == 200
        data = resp.json()
        for case in data["cases"]:
            assert "run_ids" in case
            assert isinstance(case["run_ids"], dict)

    def test_compare_with_run_ids_param(self, tmp_path):
        """测试传 run_id_{agent} 扁平参数时使用指定 run 的数据。"""
        # 创建 novel-v2 的两个 run
        setup_eval_dir(
            tmp_path,
            agent="novel-v2",
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=2.0),
        )
        setup_eval_dir(
            tmp_path,
            agent="novel-v2",
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=4.5),
        )
        # 创建 novel-v3 的一个 run
        setup_eval_dir(
            tmp_path,
            agent="novel-v3",
            run_id="model-b_2026-04-30_120000",
            report=make_report_json(total_score=3.0),
        )

        from novel_eval.viewer.server import create_app

        eval_dir = tmp_path / "eval_results"
        app = create_app(eval_dir, dev=True)
        c = TestClient(app)

        # 为 novel-v2 指定旧的 run_id
        resp = c.get(
            "/api/compare",
            params={
                "book": "凡人修仙传",
                "agents": "novel-v2,novel-v3",
                "scenario": "level_1_entity",
                "run_id_novel-v2": "model-a_2026-04-28_100000",
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        v2_summary = next(s for s in data["dimension_summary"] if s["agent"] == "novel-v2")
        v3_summary = next(s for s in data["dimension_summary"] if s["agent"] == "novel-v3")

        # novel-v2 使用指定的旧 run（total_score=2.0）
        assert v2_summary["total_score"] == 2.0
        # novel-v3 取唯一可用的 run（total_score=3.0）
        assert v3_summary["total_score"] == 3.0

    def test_compare_without_run_ids(self, tmp_path):
        """测试不传 run_id 参数时保持原有取最新 run 的行为。"""
        # 创建 novel-v2 的两个 run
        setup_eval_dir(
            tmp_path,
            agent="novel-v2",
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=2.0),
        )
        setup_eval_dir(
            tmp_path,
            agent="novel-v2",
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=4.5),
        )
        # 创建 novel-v3 的两个 run
        setup_eval_dir(
            tmp_path,
            agent="novel-v3",
            run_id="model-b_2026-04-28_100000",
            report=make_report_json(total_score=1.0),
        )
        setup_eval_dir(
            tmp_path,
            agent="novel-v3",
            run_id="model-b_2026-04-30_120000",
            report=make_report_json(total_score=3.8),
        )

        from novel_eval.viewer.server import create_app

        eval_dir = tmp_path / "eval_results"
        app = create_app(eval_dir, dev=True)
        c = TestClient(app)

        # 不传 run_id 参数
        resp = c.get(
            "/api/compare",
            params={
                "book": "凡人修仙传",
                "agents": "novel-v2,novel-v3",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        v2_summary = next(s for s in data["dimension_summary"] if s["agent"] == "novel-v2")
        v3_summary = next(s for s in data["dimension_summary"] if s["agent"] == "novel-v3")

        # 两个 agent 都应取最新 run
        assert v2_summary["total_score"] == 4.5
        assert v3_summary["total_score"] == 3.8


# ==================================================================
# 趋势 API
# ==================================================================


class TestTrendAPI:
    """时间趋势 API 测试。"""

    def test_trend_multiple_runs(self, tmp_path):
        """测试多次运行的趋势数据返回。"""
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=3.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-29_110000",
            report=make_report_json(total_score=3.5),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=4.0),
        )

        from novel_eval.viewer.server import create_app

        eval_dir = tmp_path / "eval_results"
        app = create_app(eval_dir, dev=True)
        c = TestClient(app)

        resp = c.get(
            "/api/trend",
            params={
                "book": "凡人修仙传",
                "agent": "novel-v2",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 200
        trend = resp.json()
        assert len(trend) == 3
        scores = [t["total_score"] for t in trend]
        assert scores == [3.0, 3.5, 4.0]

    def test_trend_empty_result(self, client):
        """测试无匹配运行时返回空列表。"""
        resp = client.get(
            "/api/trend",
            params={
                "book": "不存在的书",
                "agent": "novel-v2",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_trend_regression_flag(self, tmp_path):
        """测试趋势中的回归标记。"""
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=4.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-29_110000",
            report=make_report_json(total_score=2.5),
        )

        from novel_eval.viewer.server import create_app

        eval_dir = tmp_path / "eval_results"
        app = create_app(eval_dir, dev=True)
        c = TestClient(app)

        resp = c.get(
            "/api/trend",
            params={
                "book": "凡人修仙传",
                "agent": "novel-v2",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 200
        trend = resp.json()
        assert len(trend) == 2
        assert trend[0]["is_regression"] is False
        assert trend[1]["is_regression"] is True


# ==================================================================
# 索引重建 API
# ==================================================================


class TestReindexAPI:
    """索引重建 API 测试。"""

    def test_reindex_success(self, client):
        """测试触发索引重建返回成功。"""
        resp = client.post("/api/reindex")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "重建" in data["message"]

    def test_reindex_picks_new_data(self, tmp_path):
        """测试重建索引后能发现新增数据。"""
        from novel_eval.viewer.server import create_app

        # 初始为空目录
        eval_dir = tmp_path / "eval_results"
        eval_dir.mkdir()
        app = create_app(eval_dir, dev=True)
        c = TestClient(app)

        # 初始为空
        resp = c.get("/api/books")
        assert resp.json() == []

        # 添加新数据
        setup_eval_dir(
            tmp_path,
            book="新书",
            agent="test-agent",
            scenario="level_1",
            run_id="model-x_2026-04-29_120000",
        )

        # 重建索引
        resp = c.post("/api/reindex")
        assert resp.status_code == 200

        # 现在能查到
        resp = c.get("/api/books")
        books = resp.json()
        assert "新书" in books


# ==================================================================
# 跨任务类型对比校验
# ==================================================================


class TestCompareCrossTask:
    """跨 task_type 对比校验测试。"""

    def test_compare_cross_task_rejected(self, tmp_path):
        """测试跨 task_type 对比返回 400。"""
        # 创建 tool_usage 数据
        setup_eval_dir(
            tmp_path,
            agent="novel-v2",
            run_id="model-a_2026-04-29_100000",
            report=make_report_json(total_score=3.0, task_type="tool_usage"),
        )
        # 创建 skill_generation 数据（同 book、同 scenario、不同 task_type）
        setup_eval_dir(
            tmp_path,
            agent="skill-agent",
            scenario="level_1_entity",
            run_id="model-b_2026-04-29_110000",
            report=make_report_json(
                total_score=4.0,
                task_type="skill_generation",
            ),
        )

        from novel_eval.viewer.server import create_app

        eval_dir = tmp_path / "eval_results"
        app = create_app(eval_dir, dev=True)
        c = TestClient(app)

        resp = c.get(
            "/api/compare",
            params={
                "book": "凡人修仙传",
                "agents": "novel-v2,skill-agent",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 400
        assert "跨任务类型" in resp.json()["detail"]

    def test_compare_same_task_type_allowed(self, tmp_path):
        """测试相同 task_type 的对比正常返回 200。"""
        # 创建两个 tool_usage 数据
        setup_eval_dir(
            tmp_path,
            agent="novel-v2",
            run_id="model-a_2026-04-29_100000",
            report=make_report_json(total_score=3.0, task_type="tool_usage"),
        )
        setup_eval_dir(
            tmp_path,
            agent="novel-v3",
            run_id="model-b_2026-04-29_110000",
            report=make_report_json(total_score=4.0, task_type="tool_usage"),
        )

        from novel_eval.viewer.server import create_app

        eval_dir = tmp_path / "eval_results"
        app = create_app(eval_dir, dev=True)
        c = TestClient(app)

        resp = c.get(
            "/api/compare",
            params={
                "book": "凡人修仙传",
                "agents": "novel-v2,novel-v3",
                "scenario": "level_1_entity",
            },
        )
        assert resp.status_code == 200
