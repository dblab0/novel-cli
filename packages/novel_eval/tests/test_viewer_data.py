"""Eval Viewer 数据索引层测试。

覆盖 EvalDataIndex 的四组功能：
1. 索引构建（扫描目录、异常处理、重建）
2. 数据查询（list / get 系列方法）
3. 对比查询（compare 方法）
4. 时间趋势（trend 方法、回归检测）
"""

import json
from pathlib import Path

import pytest

from novel_eval.viewer.data import EvalDataIndex

# 导入 conftest 中的工厂函数
from conftest import make_case_json, make_report_json, make_messages_md, setup_eval_dir


# ==================================================================
# 索引构建测试
# ==================================================================


class TestDataIndex:
    """EvalDataIndex 索引构建测试。"""

    def test_empty_eval_dir(self, tmp_path):
        """测试空目录返回空索引。"""
        eval_dir = tmp_path / "empty"
        eval_dir.mkdir()
        index = EvalDataIndex(eval_dir)
        assert index.list_books() == []

    def test_nonexistent_eval_dir(self, tmp_path):
        """测试不存在的目录不报错，返回空索引。"""
        eval_dir = tmp_path / "nonexistent"
        index = EvalDataIndex(eval_dir)
        assert index.list_books() == []

    def test_scan_single_run(self, tmp_path):
        """测试扫描单次运行结果，正确解析目录层级。"""
        eval_dir = setup_eval_dir(tmp_path)
        index = EvalDataIndex(eval_dir)

        assert "凡人修仙传" in index.list_books()
        agents = index.list_agents("凡人修仙传")
        assert "novel-v2" in agents
        scenarios = index.list_scenarios("凡人修仙传", "novel-v2")
        assert "level_1_entity" in scenarios
        runs = index.list_runs("凡人修仙传", "novel-v2", "level_1_entity")
        assert len(runs) == 1
        assert runs[0]["run_id"] == "minimax-m2.7_2026-04-29_135831"

    def test_scan_multiple_agents(self, mock_index):
        """测试同一书籍下多个 agent 版本都能被索引。"""
        agents = mock_index.list_agents("凡人修仙传")
        assert "novel-v2" in agents
        assert "novel-v3" in agents
        assert len(agents) == 2

    def test_scan_multiple_scenarios(self, tmp_path):
        """测试同一 agent 下多个 scenario 都能被索引。"""
        eval_dir = setup_eval_dir(
            tmp_path,
            scenario="level_1_entity",
            run_id="model-a_2026-04-29_100000",
        )
        # 添加第二个 scenario
        run_dir2 = (
            eval_dir / "凡人修仙传" / "novel-v2" / "level_2_relation" / "model-a_2026-04-29_110000"
        )
        run_dir2.mkdir(parents=True)
        (run_dir2 / "report.json").write_text(
            json.dumps(make_report_json(), ensure_ascii=False), encoding="utf-8"
        )
        cases2 = run_dir2 / "cases"
        cases2.mkdir()
        (cases2 / "L2-001.json").write_text(
            json.dumps(make_case_json("L2-001"), ensure_ascii=False), encoding="utf-8"
        )

        index = EvalDataIndex(eval_dir)
        scenarios = index.list_scenarios("凡人修仙传", "novel-v2")
        assert "level_1_entity" in scenarios
        assert "level_2_relation" in scenarios
        assert len(scenarios) == 2

    def test_scan_multiple_runs(self, tmp_path):
        """测试同一 scenario 下多次运行都能被索引。"""
        eval_dir = setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_100000",
        )
        # 添加第二次运行
        run_dir2 = (
            eval_dir / "凡人修仙传" / "novel-v2" / "level_1_entity" / "model-a_2026-04-29_100000"
        )
        run_dir2.mkdir(parents=True)
        (run_dir2 / "report.json").write_text(
            json.dumps(make_report_json(total_score=3.8), ensure_ascii=False),
            encoding="utf-8",
        )
        cases2 = run_dir2 / "cases"
        cases2.mkdir()
        (cases2 / "L1-001.json").write_text(
            json.dumps(make_case_json("L1-001", total_score=3.8), ensure_ascii=False),
            encoding="utf-8",
        )

        index = EvalDataIndex(eval_dir)
        runs = index.list_runs("凡人修仙传", "novel-v2", "level_1_entity")
        assert len(runs) == 2
        # 按时间升序
        assert runs[0]["run_id"] == "model-a_2026-04-28_100000"
        assert runs[1]["run_id"] == "model-a_2026-04-29_100000"

    def test_case_subdirectory_with_messages(self, tmp_path):
        """测试 case 子目录中的 messages.md 被正确识别。"""
        eval_dir = setup_eval_dir(tmp_path, include_messages=True)
        index = EvalDataIndex(eval_dir)

        # get_case_messages 应返回内容
        messages = index.get_case_messages("minimax-m2.7_2026-04-29_135831", "L1-001")
        assert messages is not None
        assert "L1-001" in messages
        assert "韩立" in messages

    def test_case_without_messages(self, tmp_path):
        """测试没有 messages.md 的 case 不影响索引。"""
        eval_dir = setup_eval_dir(tmp_path, include_messages=False)
        index = EvalDataIndex(eval_dir)

        messages = index.get_case_messages("minimax-m2.7_2026-04-29_135831", "L1-001")
        assert messages is None

        # case 本身仍然可查
        case = index.get_case("minimax-m2.7_2026-04-29_135831", "L1-001")
        assert case is not None
        assert case["case_id"] == "L1-001"

    def test_missing_report_json(self, tmp_path):
        """测试缺少 report.json 时该 run 被跳过。"""
        # 手动创建一个没有 report.json 的 run 目录
        eval_dir = tmp_path / "eval_results"
        run_dir = (
            eval_dir / "凡人修仙传" / "novel-v2" / "level_1_entity" / "model-a_2026-04-29_100000"
        )
        run_dir.mkdir(parents=True)
        cases_dir = run_dir / "cases"
        cases_dir.mkdir()
        (cases_dir / "L1-001.json").write_text(
            json.dumps(make_case_json(), ensure_ascii=False), encoding="utf-8"
        )

        index = EvalDataIndex(eval_dir)
        assert index.list_books() == []

    def test_malformed_report_json(self, tmp_path):
        """测试 report.json 格式错误时该 run 被跳过。"""
        eval_dir = tmp_path / "eval_results"
        run_dir = (
            eval_dir / "凡人修仙传" / "novel-v2" / "level_1_entity" / "model-a_2026-04-29_100000"
        )
        run_dir.mkdir(parents=True)
        # 写入非法 JSON
        (run_dir / "report.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

        index = EvalDataIndex(eval_dir)
        assert index.list_books() == []

    def test_report_json_not_dict(self, tmp_path):
        """测试 report.json 内容不是字典类型时该 run 被跳过。"""
        eval_dir = tmp_path / "eval_results"
        run_dir = (
            eval_dir / "凡人修仙传" / "novel-v2" / "level_1_entity" / "model-a_2026-04-29_100000"
        )
        run_dir.mkdir(parents=True)
        # 写入合法 JSON 但不是字典（列表）
        (run_dir / "report.json").write_text(
            json.dumps([1, 2, 3]), encoding="utf-8"
        )

        index = EvalDataIndex(eval_dir)
        assert index.list_books() == []

    def test_malformed_case_json(self, tmp_path):
        """测试单个 case JSON 格式错误时不影响其他 case。"""
        cases = [
            make_case_json("L1-001", total_score=4.0),
            make_case_json("L1-002", total_score=3.0),
        ]
        eval_dir = setup_eval_dir(tmp_path, cases=cases)

        # 破坏 L1-001.json
        run_dir = (
            eval_dir
            / "凡人修仙传"
            / "novel-v2"
            / "level_1_entity"
            / "minimax-m2.7_2026-04-29_135831"
        )
        (run_dir / "cases" / "L1-001.json").write_text(
            "BROKEN JSON", encoding="utf-8"
        )

        index = EvalDataIndex(eval_dir)

        # L1-002 仍然能查询到
        case_002 = index.get_case("minimax-m2.7_2026-04-29_135831", "L1-002")
        assert case_002 is not None
        assert case_002["case_id"] == "L1-002"

    def test_invalid_run_dir_name(self, tmp_path):
        """测试不符合 {model}_{timestamp} 命名规范的目录被跳过。"""
        eval_dir = tmp_path / "eval_results"
        # 名字不符合格式的 run 目录
        run_dir = (
            eval_dir / "凡人修仙传" / "novel-v2" / "level_1_entity" / "invalid_name"
        )
        run_dir.mkdir(parents=True)
        (run_dir / "report.json").write_text(
            json.dumps(make_report_json(), ensure_ascii=False), encoding="utf-8"
        )

        index = EvalDataIndex(eval_dir)
        assert index.list_books() == []

    def test_nested_extra_dirs_ignored(self, tmp_path):
        """测试非标准层级（过深或过浅）的目录被忽略。"""
        eval_dir = tmp_path / "eval_results"
        # 只有 3 层（缺 scenario 层）
        shallow_dir = eval_dir / "凡人修仙传" / "novel-v2" / "model-a_2026-04-29_100000"
        shallow_dir.mkdir(parents=True)
        (shallow_dir / "report.json").write_text(
            json.dumps(make_report_json(), ensure_ascii=False), encoding="utf-8"
        )

        index = EvalDataIndex(eval_dir)
        assert index.list_books() == []

    def test_reindex(self, tmp_path):
        """测试 rebuild() 方法能重新扫描目录并更新索引。"""
        # 初始为空
        eval_dir = tmp_path / "eval_results"
        eval_dir.mkdir()
        index = EvalDataIndex(eval_dir)
        assert index.list_books() == []

        # 添加数据
        setup_eval_dir(
            tmp_path,
            book="新书",
            agent="test-agent",
            scenario="level_1",
            run_id="model-x_2026-04-29_120000",
        )

        # 重建索引
        index.rebuild()
        assert "新书" in index.list_books()

    def test_multiple_books(self, tmp_path):
        """测试多本书籍被同时索引。"""
        setup_eval_dir(tmp_path, book="凡人修仙传")
        setup_eval_dir(
            tmp_path,
            book="遮天",
            agent="novel-v2",
            scenario="level_1_entity",
            run_id="model-a_2026-04-29_100000",
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)
        books = index.list_books()
        assert "凡人修仙传" in books
        assert "遮天" in books
        assert len(books) == 2


# ==================================================================
# 数据查询测试
# ==================================================================


class TestDataQuery:
    """索引数据查询测试。"""

    def test_list_books(self, mock_index):
        """测试 list_books() 返回所有书籍名称。"""
        books = mock_index.list_books()
        assert books == ["凡人修仙传"]

    def test_list_agents(self, mock_index):
        """测试 list_agents() 返回指定书籍的 agent 列表。"""
        agents = mock_index.list_agents("凡人修仙传")
        assert sorted(agents) == ["novel-v2", "novel-v3"]

    def test_list_agents_empty(self, mock_index):
        """测试查询不存在的书籍时返回空列表。"""
        agents = mock_index.list_agents("不存在的书")
        assert agents == []

    def test_list_scenarios(self, mock_index):
        """测试 list_scenarios() 返回指定条件的 scenario 列表。"""
        scenarios = mock_index.list_scenarios("凡人修仙传", "novel-v2")
        assert scenarios == ["level_1_entity"]

    def test_list_scenarios_empty(self, mock_index):
        """测试查询不存在的 agent 时返回空列表。"""
        scenarios = mock_index.list_scenarios("凡人修仙传", "not-exist")
        assert scenarios == []

    def test_list_runs(self, mock_index):
        """测试 list_runs() 返回指定条件的运行列表。"""
        runs = mock_index.list_runs("凡人修仙传", "novel-v2", "level_1_entity")
        assert len(runs) == 1
        assert runs[0]["run_id"] == "minimax-m2.7_2026-04-29_135831"
        assert runs[0]["model"] == "minimax-m2.7"

    def test_list_runs_sorted_by_time(self, tmp_path):
        """测试 list_runs() 结果按时间戳升序排列。"""
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_100000",
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-30_120000",
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-29_110000",
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)
        runs = index.list_runs("凡人修仙传", "novel-v2", "level_1_entity")
        assert len(runs) == 3
        timestamps = [r["timestamp"] for r in runs]
        assert timestamps == sorted(timestamps)

    def test_list_cases_for_run(self, mock_index):
        """测试 list_cases_for_run() 返回用例摘要列表。"""
        run_id = "minimax-m2.7_2026-04-29_135831"
        cases = mock_index.list_cases_for_run(run_id)
        assert len(cases) == 1
        assert cases[0]["case_id"] == "L1-001"
        assert cases[0]["total_score"] == 3.5
        assert cases[0]["passed"] is True  # 3.5 >= 3.0

    def test_list_cases_for_run_v3(self, mock_index):
        """测试 novel-v3 的用例列表包含两个用例。"""
        run_id = "minimax-m2.7_2026-04-30_115625"
        cases = mock_index.list_cases_for_run(run_id)
        assert len(cases) == 2
        case_ids = [c["case_id"] for c in cases]
        assert "L1-001" in case_ids
        assert "L1-002" in case_ids

    def test_list_cases_nonexistent_run(self, mock_index):
        """测试查询不存在的 run_id 返回空列表。"""
        cases = mock_index.list_cases_for_run("nonexistent_run")
        assert cases == []

    def test_get_report(self, mock_index):
        """测试 get_report() 返回 report.json 数据。"""
        run_id = "minimax-m2.7_2026-04-29_135831"
        report = mock_index.get_report(run_id)
        assert report is not None
        assert report["total_score"] == 3.5
        assert report["total_cases"] == 1

    def test_get_report_v3(self, mock_index):
        """测试获取 novel-v3 的 report。"""
        run_id = "minimax-m2.7_2026-04-30_115625"
        report = mock_index.get_report(run_id)
        assert report is not None
        assert report["total_score"] == 4.0
        assert report["total_cases"] == 2

    def test_get_case(self, mock_index):
        """测试 get_case() 返回用例完整数据。"""
        # novel-v3 的 L1-002 case
        case = mock_index.get_case("minimax-m2.7_2026-04-30_115625", "L1-002")
        assert case is not None
        assert case["case_id"] == "L1-002"
        assert case["question"] == "测试问题"
        assert "score" in case

    def test_get_case_messages(self, tmp_path):
        """测试 get_case_messages() 返回 messages.md 内容。"""
        eval_dir = setup_eval_dir(tmp_path, include_messages=True)
        index = EvalDataIndex(eval_dir)
        messages = index.get_case_messages("minimax-m2.7_2026-04-29_135831", "L1-001")
        assert messages is not None
        assert "韩立" in messages

    def test_get_case_messages_not_found(self, mock_index):
        """测试获取不存在 messages.md 时返回 None。"""
        # novel-v3 的 L1-001 和 L1-002 都无 messages.md
        messages = mock_index.get_case_messages("minimax-m2.7_2026-04-30_115625", "L1-001")
        assert messages is None
        messages2 = mock_index.get_case_messages("minimax-m2.7_2026-04-30_115625", "L1-002")
        assert messages2 is None

    def test_get_nonexistent_run(self, mock_index):
        """测试查询不存在的 run 返回 None。"""
        report = mock_index.get_report("nonexistent_run_2026-01-01_000000")
        assert report is None

    def test_get_nonexistent_case(self, mock_index):
        """测试查询不存在的 case 返回 None。"""
        case = mock_index.get_case("minimax-m2.7_2026-04-30_115625", "NONEXISTENT-999")
        assert case is None

    def test_get_nonexistent_case_messages(self, mock_index):
        """测试查询不存在的 case 的 messages 返回 None。"""
        messages = mock_index.get_case_messages("minimax-m2.7_2026-04-30_115625", "NONEXISTENT-999")
        assert messages is None

    def test_get_run_info(self, mock_index):
        """测试 get_run_info() 返回运行元数据。"""
        info = mock_index.get_run_info("minimax-m2.7_2026-04-29_135831")
        assert info is not None
        assert info["book"] == "凡人修仙传"
        assert info["agent"] == "novel-v2"
        assert "model" in info
        assert "timestamp" in info

    def test_get_run_info_not_found(self, mock_index):
        """测试 get_run_info() 对不存在的 run_id 返回 None。"""
        info = mock_index.get_run_info("nonexistent_2026-01-01_000000")
        assert info is None


# ==================================================================
# 对比查询测试
# ==================================================================


class TestDataCompare:
    """对比数据查询测试。"""

    def test_compare_multiple_agents(self, mock_index):
        """测试对比多个 agent 的最新运行结果。"""
        result = mock_index.compare(
            "凡人修仙传", ["novel-v2", "novel-v3"], "level_1_entity"
        )
        # 验证返回结构
        assert "cases" in result
        assert "dimension_summary" in result

        # 验证维度汇总
        summary = result["dimension_summary"]
        agents_in_summary = {s["agent"] for s in summary}
        assert "novel-v2" in agents_in_summary
        assert "novel-v3" in agents_in_summary

        # 验证各 agent 总分
        v2_summary = next(s for s in summary if s["agent"] == "novel-v2")
        v3_summary = next(s for s in summary if s["agent"] == "novel-v3")
        assert v2_summary["total_score"] == 3.5
        assert v3_summary["total_score"] == 4.0

    def test_compare_single_agent(self, mock_index):
        """测试对比单个 agent。"""
        result = mock_index.compare(
            "凡人修仙传", ["novel-v2"], "level_1_entity"
        )
        assert len(result["dimension_summary"]) == 1
        assert result["dimension_summary"][0]["agent"] == "novel-v2"
        assert result["dimension_summary"][0]["total_score"] == 3.5

    def test_compare_missing_agent(self, mock_index):
        """测试对比中包含无数据的 agent。"""
        result = mock_index.compare(
            "凡人修仙传", ["novel-v2", "nonexistent-agent"], "level_1_entity"
        )
        # novel-v2 有数据
        v2_summary = next(s for s in result["dimension_summary"] if s["agent"] == "novel-v2")
        assert v2_summary["total_score"] == 3.5

        # 无数据的 agent 总分为 0
        missing_summary = next(s for s in result["dimension_summary"] if s["agent"] == "nonexistent-agent")
        assert missing_summary["total_score"] == 0.0
        assert missing_summary["dimensions"] == {}

    def test_compare_different_scenarios(self, tmp_path):
        """测试不同 scenario 下对比返回对应数据。"""
        setup_eval_dir(
            tmp_path,
            scenario="level_1_entity",
            run_id="model-a_2026-04-29_100000",
        )
        setup_eval_dir(
            tmp_path,
            scenario="level_2_relation",
            run_id="model-a_2026-04-29_110000",
            report=make_report_json(total_score=2.5),
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)

        # 对比 level_1_entity
        result = index.compare("凡人修仙传", ["novel-v2"], "level_1_entity")
        assert result["dimension_summary"][0]["total_score"] == 3.5

        # 对比 level_2_relation
        result2 = index.compare("凡人修仙传", ["novel-v2"], "level_2_relation")
        assert result2["dimension_summary"][0]["total_score"] == 2.5

    def test_compare_picks_latest_run(self, tmp_path):
        """测试 compare 选取最新一次运行（按时间戳降序取第一个）。"""
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=2.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=4.5),
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)

        result = index.compare("凡人修仙传", ["novel-v2"], "level_1_entity")
        # 应取 2026-04-30 的运行（total_score=4.5）
        assert result["dimension_summary"][0]["total_score"] == 4.5

    def test_compare_with_explicit_run_ids(self, tmp_path):
        """测试指定 run_ids 参数时，使用指定的 run 而非最新。"""
        # 创建同一 agent 的两个 run：旧的 total_score=2.0，新的 total_score=4.5
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=2.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=4.5),
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)

        # 指定使用旧的 run_id
        result = index.compare(
            "凡人修仙传", ["novel-v2"], "level_1_entity",
            run_ids={"novel-v2": "model-a_2026-04-28_100000"},
        )
        # 应使用旧 run 的数据（total_score=2.0），而非最新的 4.5
        assert result["dimension_summary"][0]["total_score"] == 2.0

    def test_compare_with_partial_run_ids(self, tmp_path):
        """测试只指定部分 agent 的 run_id，其余仍取最新 run。"""
        # agent-a 有两个 run
        setup_eval_dir(
            tmp_path,
            agent="agent-a",
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=2.0),
        )
        setup_eval_dir(
            tmp_path,
            agent="agent-a",
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=4.5),
        )
        # agent-b 有两个 run
        setup_eval_dir(
            tmp_path,
            agent="agent-b",
            run_id="model-b_2026-04-28_100000",
            report=make_report_json(total_score=1.5),
        )
        setup_eval_dir(
            tmp_path,
            agent="agent-b",
            run_id="model-b_2026-04-30_120000",
            report=make_report_json(total_score=3.8),
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)

        # 只为 agent-a 指定旧的 run_id，agent-b 不指定（应取最新）
        result = index.compare(
            "凡人修仙传", ["agent-a", "agent-b"], "level_1_entity",
            run_ids={"agent-a": "model-a_2026-04-28_100000"},
        )

        summary_a = next(s for s in result["dimension_summary"] if s["agent"] == "agent-a")
        summary_b = next(s for s in result["dimension_summary"] if s["agent"] == "agent-b")

        # agent-a 使用指定的旧 run（total_score=2.0）
        assert summary_a["total_score"] == 2.0
        # agent-b 取最新 run（total_score=3.8）
        assert summary_b["total_score"] == 3.8

    def test_compare_with_invalid_run_id(self, tmp_path):
        """测试指定不存在的 run_id 时，该 agent 在对比结果中无数据。"""
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=4.0),
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)

        # 指定一个不存在的 run_id
        result = index.compare(
            "凡人修仙传", ["novel-v2"], "level_1_entity",
            run_ids={"novel-v2": "nonexistent_2026-01-01_000000"},
        )

        # 该 agent 无数据，总分应为 0
        summary = result["dimension_summary"]
        assert len(summary) == 1
        assert summary[0]["agent"] == "novel-v2"
        assert summary[0]["total_score"] == 0.0
        assert summary[0]["dimensions"] == {}


# ==================================================================
# 时间趋势测试
# ==================================================================


class TestDataTrend:
    """时间趋势数据查询测试。"""

    def test_trend_multiple_runs(self, tmp_path):
        """测试同一 scenario 下多次运行生成趋势数据。"""
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

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)
        trend = index.trend("凡人修仙传", "novel-v2", "level_1_entity")

        assert len(trend) == 3
        scores = [t["total_score"] for t in trend]
        assert scores == [3.0, 3.5, 4.0]

    def test_trend_sorted_by_time(self, tmp_path):
        """测试趋势数据按时间戳升序排列。"""
        # 以非顺序方式创建
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=4.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=2.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-29_110000",
            report=make_report_json(total_score=3.0),
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)
        trend = index.trend("凡人修仙传", "novel-v2", "level_1_entity")

        timestamps = [t["timestamp"] for t in trend]
        assert timestamps == [
            "2026-04-28_100000",
            "2026-04-29_110000",
            "2026-04-30_120000",
        ]

    def test_trend_regression_detection(self, tmp_path):
        """测试回归检测：分数下降时标记 is_regression=True。"""
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_100000",
            report=make_report_json(total_score=4.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-29_110000",
            report=make_report_json(total_score=2.5),  # 回归
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-30_120000",
            report=make_report_json(total_score=3.0),  # 恢复
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)
        trend = index.trend("凡人修仙传", "novel-v2", "level_1_entity")

        assert trend[0]["is_regression"] is False  # 4.0（第一次无前值）
        assert trend[1]["is_regression"] is True   # 2.5 < 4.0
        assert trend[2]["is_regression"] is False  # 3.0 > 2.5

    def test_trend_single_run(self, tmp_path):
        """测试只有一次运行时趋势列表只有一项。"""
        setup_eval_dir(tmp_path)
        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)

        trend = index.trend("凡人修仙传", "novel-v2", "level_1_entity")
        assert len(trend) == 1
        assert trend[0]["is_regression"] is False

    def test_trend_no_runs(self, tmp_path):
        """测试无运行时趋势列表为空。"""
        eval_dir = tmp_path / "empty"
        eval_dir.mkdir()
        index = EvalDataIndex(eval_dir)

        trend = index.trend("凡人修仙传", "novel-v2", "level_1_entity")
        assert trend == []

    def test_trend_includes_dimension_scores(self, tmp_path):
        """测试趋势数据包含 dimension_scores。"""
        dims = {
            "tool_selection": 4.0,
            "param_quality": 3.0,
            "call_efficiency": 3.5,
            "result_utilization": 4.0,
        }
        setup_eval_dir(
            tmp_path,
            report=make_report_json(dimension_scores=dims),
        )
        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)

        trend = index.trend("凡人修仙传", "novel-v2", "level_1_entity")
        assert len(trend) == 1
        assert trend[0]["dimension_scores"] == dims

    def test_trend_includes_pass_rate(self, tmp_path):
        """测试趋势数据包含 pass_rate。"""
        setup_eval_dir(
            tmp_path,
            report=make_report_json(pass_rate=0.75),
        )
        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)

        trend = index.trend("凡人修仙传", "novel-v2", "level_1_entity")
        assert len(trend) == 1
        assert trend[0]["pass_rate"] == 0.75

    def test_trend_consecutive_regression(self, tmp_path):
        """测试连续回归场景：每个分数都低于前一次。"""
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-27_100000",
            report=make_report_json(total_score=4.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-28_110000",
            report=make_report_json(total_score=3.0),
        )
        setup_eval_dir(
            tmp_path,
            run_id="model-a_2026-04-29_120000",
            report=make_report_json(total_score=2.0),
        )

        eval_dir = tmp_path / "eval_results"
        index = EvalDataIndex(eval_dir)
        trend = index.trend("凡人修仙传", "novel-v2", "level_1_entity")

        assert trend[0]["is_regression"] is False  # 首次
        assert trend[1]["is_regression"] is True   # 3.0 < 4.0
        assert trend[2]["is_regression"] is True   # 2.0 < 3.0


# ==================================================================
# task_type 字段测试
# ==================================================================


class TestTaskType:
    """RunInfo.task_type 字段传播与 fallback 测试。"""

    def test_task_type_from_report_data(self, tmp_path):
        """测试 task_type 从 report.json 正确传播到 RunInfo。"""
        report = make_report_json(task_type="skill_generation")
        eval_dir = setup_eval_dir(tmp_path, report=report)
        index = EvalDataIndex(eval_dir)

        # 通过内部 _runs 字典直接验证 RunInfo.task_type
        run_id = "minimax-m2.7_2026-04-29_135831"
        run_info = index._runs.get(run_id)
        assert run_info is not None
        assert run_info.task_type == "skill_generation"

    def test_task_type_default_when_missing(self, tmp_path):
        """测试 report.json 无 task_type 字段时 fallback 到 tool_usage。"""
        # make_report_json(task_type=None) 不写入 task_type 字段
        report = make_report_json(task_type=None)
        eval_dir = setup_eval_dir(tmp_path, report=report)
        index = EvalDataIndex(eval_dir)

        run_id = "minimax-m2.7_2026-04-29_135831"
        run_info = index._runs.get(run_id)
        assert run_info is not None
        assert run_info.task_type == "tool_usage"

    def test_task_type_tool_usage_explicit(self, tmp_path):
        """测试显式设置 task_type=tool_usage 时正确存储。"""
        report = make_report_json(task_type="tool_usage")
        eval_dir = setup_eval_dir(tmp_path, report=report)
        index = EvalDataIndex(eval_dir)

        run_id = "minimax-m2.7_2026-04-29_135831"
        run_info = index._runs.get(run_id)
        assert run_info is not None
        assert run_info.task_type == "tool_usage"

    def test_task_type_with_dimensions(self, tmp_path):
        """测试 task_type 与 dimensions 字段同时存在时正确读取。"""
        dims = [
            {"key": "tool_selection", "label": "工具选择"},
            {"key": "param_quality", "label": "参数质量"},
        ]
        report = make_report_json(task_type="skill_generation", dimensions=dims)
        eval_dir = setup_eval_dir(tmp_path, report=report)
        index = EvalDataIndex(eval_dir)

        run_id = "minimax-m2.7_2026-04-29_135831"
        run_info = index._runs.get(run_id)
        assert run_info is not None
        assert run_info.task_type == "skill_generation"
        assert run_info.report_data is not None
        assert run_info.report_data["dimensions"] == dims

    def test_task_type_preserved_after_rebuild(self, tmp_path):
        """测试 rebuild() 后 task_type 仍然正确。"""
        report = make_report_json(task_type="skill_generation")
        eval_dir = setup_eval_dir(tmp_path, report=report)
        index = EvalDataIndex(eval_dir)

        run_id = "minimax-m2.7_2026-04-29_135831"
        assert index._runs[run_id].task_type == "skill_generation"

        # 重建索引
        index.rebuild()
        assert index._runs[run_id].task_type == "skill_generation"
