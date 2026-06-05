"""技能生成评估任务单元测试。

覆盖范围：
- Generator: CSV 解析、随机抽取、seed 复现、query 模板、追加模式
- Judge: 5 维度解析、SKILL.md 加载、边界情况
- Task: _prepare_work_dir、初始化、完整流程 mock
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from novel_eval.models import CaseResult, EvalCase, EvalScore, ToolCallRecord
from novel_eval.tasks.skill_generation.generator import (
    _parse_csv,
    _sample_entities,
    generate_cases,
    load_cases_yaml,
    save_cases_yaml,
    save_cases_yaml_append,
)
from novel_eval.tasks.skill_generation.judge import (
    _parse_judge_response,
    _extract_scores_from_json,
    judge_case,
    load_skill_template,
    _SCORE_DIMENSIONS,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    """创建包含多种实体类型的测试 CSV 文件。"""
    csv_content = (
        "id,name,type,description,alias,book\n"
        "凡人修仙传_人物_韩立_0,韩立,人物,"
        "\"[{'1': '资质平庸的少年'}, {'2': '修炼长春功'}, "
        "{'3': '获得掌天瓶'}, {'4': '加入黄枫谷'}, {'5': '结丹成功'}, "
        "{'6': '到达乱星海'}, {'7': '获得虚天鼎'}, {'8': '化神成功'}, "
        "{'9': '灵界飞升'}, {'10': '修炼至大乘'}, {'11': '仙界飞升'}, "
        "{'12': '成为时间道祖'}, {'13': '获得混沌之血'}, {'14': '修炼法则'}, "
        "{'15': '获得玄天斩灵剑'}, {'16': '击败敌人'}, {'17': '获得天狐变'}, "
        "{'18': '收南宫婉为妻'}, {'19': '获得噬金虫'}, {'20': '修炼元磁神山'}, "
        "{'21': '获得金雷竹'}]\",[],凡人修仙传\n"
        "凡人修仙传_人物_南宫婉_0,南宫婉,人物,"
        "\"[{'1': '韩立的妻子'}, {'2': '掩月宗女修'}, "
        "{'3': '结丹期修士'}, {'4': '冰清玉洁'}, {'5': '灵根为水'}, "
        "{'6': '修炼冰魄神光'}, {'7': '乱星海重逢'}, {'8': '元婴期'}, "
        "{'9': '灵界同行'}, {'10': '仙人转世'}, {'11': '与韩立重逢'}, "
        "{'12': '继承冰凤血脉'}, {'13': '修炼至化神'}, {'14': '灵界定居'}, "
        "{'15': '仙界重逢'}, {'16': '获得冰凤真灵'}, {'17': '元婴后期'}, "
        "{'18': '结为道侣'}, {'19': '获得功法传承'}, {'20': '达到合体期'}, "
        "{'21': '仙界修炼'}]\",[],凡人修仙传\n"
        "凡人修仙传_人物_路人甲_0,路人甲,人物,"
        "\"[{'1': '路过的路人'}]\",[],凡人修仙传\n"
        "凡人修仙传_物品_虚天鼎_0,虚天鼎,物品,"
        "\"[{'1': '通天灵宝'}, {'2': '虚天殿内殿获得'}, "
        "{'3': '可以炼化真灵之血'}, {'4': '可以收纳灵兽'}, "
        "{'5': '空间类法宝'}, {'6': '多次救韩立于危难'}, "
        "{'7': '可自行吸收灵气'}, {'8': '内含玄天之宝'}, "
        "{'9': '被极阴祖师觊觎'}, {'10': '需要特定功法催动'}, "
        "{'11': '可放出玄天斩灵剑'}, {'12': '内含乾坤阵'}, "
        "{'13': '能炼制丹药'}, {'14': '可吞噬其他法宝'}, "
        "{'15': '可防御攻击'}, {'16': '被万天明争夺'}, "
        "{'17': '收纳噬金虫'}, {'18': '被黑炎妖王攻击'}, "
        "{'19': '空间巨大'}, {'20': '韩立第一件重宝'}, "
        "{'21': '具有器灵'}]\",[],凡人修仙传\n"
        "凡人修仙传_地点_黄枫谷_0,黄枫谷,地点,"
        "\"[{'1': '韩立早期门派'}, {'2': '位于越国'}]\",[],凡人修仙传\n"
        "凡人修仙传_组织_七玄门_0,七玄门,组织,"
        "\"[{'1': '韩立的第一个门派'}, {'2': '位于镜州'}]\",[],凡人修仙传\n"
        "凡人修仙传_技能_长春功_0,长春功,技能,"
        "\"[{'1': '韩立修炼的基础功法'}, {'2': '四属性灵根'}]\",[],凡人修仙传\n"
    )
    csv_path = tmp_path / "凡人修仙传" / "凡人修仙传_entities.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(csv_content, encoding="utf-8")
    return csv_path


@pytest.fixture
def sample_entities() -> list[dict]:
    """创建测试用的实体列表。"""
    return [
        {
            "id": "凡人修仙传_人物_韩立_0",
            "name": "韩立",
            "type": "人物",
            "descriptions": [{"1": "资质平庸"}, {"2": "修炼长春功"}] * 15,
            "description_count": 30,
        },
        {
            "id": "凡人修仙传_人物_南宫婉_0",
            "name": "南宫婉",
            "type": "人物",
            "descriptions": [{"1": "韩立妻子"}] * 25,
            "description_count": 25,
        },
        {
            "id": "凡人修仙传_人物_路人甲_0",
            "name": "路人甲",
            "type": "人物",
            "descriptions": [{"1": "路人"}],
            "description_count": 1,
        },
        {
            "id": "凡人修仙传_物品_虚天鼎_0",
            "name": "虚天鼎",
            "type": "物品",
            "descriptions": [{"1": "通天灵宝"}] * 22,
            "description_count": 22,
        },
        {
            "id": "凡人修仙传_地点_黄枫谷_0",
            "name": "黄枫谷",
            "type": "地点",
            "descriptions": [{"1": "门派"}] * 5,
            "description_count": 5,
        },
    ]


# ------------------------------------------------------------------
# 6.1 Generator 测试
# ------------------------------------------------------------------


class TestParseCSV:
    """CSV 解析测试。"""

    def test_parse_normal_csv(self, sample_csv: Path):
        """正常 CSV 解析。"""
        entities = _parse_csv(sample_csv)
        assert len(entities) == 7

        # 验证韩立实体
        hanli = next(e for e in entities if e["name"] == "韩立")
        assert hanli["id"] == "凡人修仙传_人物_韩立_0"
        assert hanli["type"] == "人物"
        assert hanli["description_count"] == 21
        assert len(hanli["descriptions"]) == 21

    def test_parse_csv_handles_bom(self, tmp_path: Path):
        """CSV 包含 BOM 时能正确解析。"""
        csv_content = "id,name,type,description,alias,book\n凡人修仙传_人物_韩立_0,韩立,人物,\"[{'1': '测试'}]\",[],凡人修仙传\n"
        csv_path = tmp_path / "test.csv"
        csv_path.write_bytes(b"\xef\xbb\xbf" + csv_content.encode("utf-8"))

        entities = _parse_csv(csv_path)
        assert len(entities) == 1

    def test_parse_csv_skips_invalid_description(self, tmp_path: Path):
        """description 格式异常时跳过该实体。"""
        csv_content = (
            "id,name,type,description,alias,book\n"
            "凡人修仙传_人物_正常_0,正常,人物,\"[{'1': '正常描述'}]\",[],凡人修仙传\n"
            "凡人修仙传_人物_异常_0,异常,人物,invalid_python_literal,[],凡人修仙传\n"
        )
        csv_path = tmp_path / "test.csv"
        csv_path.write_text(csv_content, encoding="utf-8")

        entities = _parse_csv(csv_path)
        assert len(entities) == 1
        assert entities[0]["name"] == "正常"

    def test_parse_nonexistent_file(self, tmp_path: Path):
        """CSV 文件不存在时返回空列表。"""
        entities = _parse_csv(tmp_path / "nonexistent.csv")
        assert entities == []


class TestSampleEntities:
    """随机抽取测试。"""

    def test_sample_normal(self, sample_entities: list[dict]):
        """正常抽取：人物类型有 2 个描述>20 的实体，物品有 1 个。"""
        result = _sample_entities(sample_entities, min_descriptions=20, samples_per_type=5, seed=42)
        assert "人物" in result
        assert len(result["人物"]) == 2
        assert "物品" in result  # 虚天鼎 22 > 20
        assert len(result["物品"]) == 1

    def test_sample_insufficient_entities(self, sample_entities: list[dict]):
        """可用实体不足时取全部。"""
        result = _sample_entities(sample_entities, min_descriptions=20, samples_per_type=10, seed=42)
        assert "物品" in result  # 虚天鼎 22 > 20
        assert len(result["物品"]) == 1  # 只有 1 个，不足 samples_per_type=10

    def test_sample_seed_reproducibility(self, sample_entities: list[dict]):
        """相同 seed 结果完全一致。"""
        result1 = _sample_entities(sample_entities, min_descriptions=20, samples_per_type=5, seed=42)
        result2 = _sample_entities(sample_entities, min_descriptions=20, samples_per_type=5, seed=42)
        for entity_type in result1:
            names1 = [e["name"] for e in result1[entity_type]]
            names2 = [e["name"] for e in result2[entity_type]]
            assert names1 == names2

    def test_sample_only_supported_types(self, sample_entities: list[dict]):
        """只抽取支持的类型（人物/物品/地点/组织/技能）。"""
        # 添加一个不支持的类型
        sample_entities.append({
            "id": "test_unknown",
            "name": "未知类型",
            "type": "未知",
            "descriptions": [{"1": "描述"}] * 25,
            "description_count": 25,
        })
        result = _sample_entities(sample_entities, min_descriptions=20, samples_per_type=5, seed=42)
        assert "未知" not in result


class TestGenerateCases:
    """generate_cases 集成测试。"""

    def test_generate_cases_full_flow(self, sample_csv: Path, tmp_path: Path):
        """完整流程：CSV → 抽取 → 生成 EvalCase。"""
        data_dir = tmp_path
        cases = generate_cases(
            book="凡人修仙传",
            data_dir=data_dir,
            min_descriptions=20,
            samples_per_type=5,
            seed=42,
        )

        assert len(cases) > 0

        # 检查 case 结构
        for case in cases:
            assert case.id.startswith("SG-")
            assert case.book == "凡人修仙传"
            assert "/skill:" in case.question
            assert "entity_id" in case.meta
            assert "skill_type" in case.meta
            assert "output_path" in case.meta
            assert "description_count" in case.meta

    def test_generate_cases_query_template(self, sample_csv: Path, tmp_path: Path):
        """query 模板正确生成。"""
        cases = generate_cases(
            book="凡人修仙传",
            data_dir=tmp_path,
            min_descriptions=20,
            samples_per_type=5,
            seed=42,
        )

        # 查找人物类型的 case
        char_cases = [c for c in cases if c.meta["skill_type"] == "generate-character"]
        for case in char_cases:
            assert case.question.startswith("/skill:generate-character 检索")
            assert case.question.endswith("相关信息后生成角色设定")


class TestYamlIO:
    """YAML 保存/加载测试。"""

    def test_save_and_load_yaml(self, tmp_path: Path):
        """保存后再加载应得到相同结果。"""
        cases = [
            EvalCase(
                id="SG-CHAR-001",
                book="凡人修仙传",
                question="/skill:generate-character 检索test 相关信息后生成角色设定",
                meta={
                    "entity_id": "test_entity",
                    "entity_name": "测试实体",
                    "entity_type": "人物",
                    "skill_type": "generate-character",
                    "output_path": "characters/test_entity.md",
                    "description_count": 30,
                    "descriptions": [{"1": "描述1"}],
                },
            ),
        ]

        paths = save_cases_yaml(tmp_path, cases, "凡人修仙传")
        assert len(paths) == 1
        assert paths[0].exists()

        loaded = load_cases_yaml(paths[0])
        assert len(loaded) == 1
        assert loaded[0].id == "SG-CHAR-001"
        assert loaded[0].question == cases[0].question

    def test_save_multiple_skill_types(self, tmp_path: Path):
        """按 skill 类型生成独立文件。"""
        cases = [
            EvalCase(
                id="SG-CHAR-001",
                book="凡人修仙传",
                question="q1",
                meta={"skill_type": "generate-character", "entity_id": "e1"},
            ),
            EvalCase(
                id="SG-ITEM-001",
                book="凡人修仙传",
                question="q2",
                meta={"skill_type": "generate-item", "entity_id": "e2"},
            ),
        ]

        paths = save_cases_yaml(tmp_path, cases, "凡人修仙传")
        assert len(paths) == 2
        filenames = [p.name for p in paths]
        assert "skill_gen_character.yaml" in filenames
        assert "skill_gen_item.yaml" in filenames

    def test_append_mode(self, tmp_path: Path):
        """追加模式排除已有 entity_id 并重排 ID。"""
        # 先保存一批
        existing = [
            EvalCase(
                id="SG-CHAR-001",
                book="凡人修仙传",
                question="q1",
                meta={
                    "skill_type": "generate-character",
                    "entity_id": "entity_1",
                },
            ),
        ]
        save_cases_yaml(tmp_path, existing, "凡人修仙传")

        # 追加新 case（排除 entity_1）
        new_cases = [
            EvalCase(
                id="SG-CHAR-002",
                book="凡人修仙传",
                question="q2",
                meta={
                    "skill_type": "generate-character",
                    "entity_id": "entity_2",
                },
            ),
            # 这个 entity_id 与已有相同，应被排除
            EvalCase(
                id="SG-CHAR-003",
                book="凡人修仙传",
                question="q1_dup",
                meta={
                    "skill_type": "generate-character",
                    "entity_id": "entity_1",
                },
            ),
        ]

        paths = save_cases_yaml_append(tmp_path, new_cases, "凡人修仙传")
        loaded = load_cases_yaml(paths[0])

        # 应有 2 个用例（原有 1 个 + 新增 1 个，entity_1 被排除）
        assert len(loaded) == 2

        # ID 应被重排
        ids = [c.id for c in loaded]
        assert ids == ["SG-CHAR-001", "SG-CHAR-002"]


# ------------------------------------------------------------------
# 6.2 Judge 测试
# ------------------------------------------------------------------


class TestJudgeParse:
    """Judge 响应解析测试。"""

    def test_parse_valid_json(self):
        """直接 JSON 解析。"""
        response = '{"tool_selection": 4, "param_quality": 3, "call_efficiency": 5, "setting_completeness": 4, "format_compliance": 5, "commentary": "评分"}'
        scores, commentary = _parse_judge_response(response)
        assert scores["tool_selection"] == 4
        assert scores["setting_completeness"] == 4
        assert commentary == "评分"

    def test_parse_json_code_block(self):
        """从 ```json``` 代码块中提取。"""
        response = '```json\n{"tool_selection": 3, "param_quality": 4, "call_efficiency": 3, "setting_completeness": 5, "format_compliance": 4, "commentary": "代码块评分"}\n```'
        scores, commentary = _parse_judge_response(response)
        assert scores["tool_selection"] == 3
        assert scores["setting_completeness"] == 5

    def test_parse_fallback(self):
        """无法解析时返回默认 3 分。"""
        response = "这不是JSON"
        scores, commentary = _parse_judge_response(response)
        for k in _SCORE_DIMENSIONS:
            assert scores[k] == 3

    def test_score_clamping(self):
        """分数约束在 1-5 范围。"""
        data = {"tool_selection": 10, "param_quality": -1, "call_efficiency": 3, "setting_completeness": 6, "format_compliance": 0, "commentary": "test"}
        scores, _ = _extract_scores_from_json(data)
        assert scores["tool_selection"] == 5
        assert scores["param_quality"] == 1
        assert scores["setting_completeness"] == 5
        assert scores["format_compliance"] == 1

    def test_missing_dimensions_get_default(self):
        """缺少维度时使用默认 3 分。"""
        data = {"tool_selection": 4, "commentary": "test"}
        scores, _ = _extract_scores_from_json(data)
        assert scores["tool_selection"] == 4
        assert scores["param_quality"] == 3

    def test_5_dimensions_in_response(self):
        """响应包含完整的 5 个维度。"""
        response = json.dumps({
            "tool_selection": 4,
            "param_quality": 3,
            "call_efficiency": 5,
            "setting_completeness": 4,
            "format_compliance": 5,
            "commentary": "完整评分",
        })
        scores, _ = _parse_judge_response(response)
        assert len(scores) == 5
        assert "setting_completeness" in scores
        assert "format_compliance" in scores


class TestJudgeCase:
    """judge_case 评分测试。"""

    def test_no_tool_calls(self):
        """无工具调用：全部 1 分（早返，不调用 LLM）。"""
        case_result = CaseResult(
            case_id="test-001",
            question="测试问题",
            tool_calls=[],
            final_answer="",
        )
        score = judge_case(case_result, {"base_url": "", "api_key": ""})
        assert all(v == 1 for v in score.scores.values())
        assert score.total_score == 1.0


class TestLoadSkillTemplate:
    """SKILL.md 加载测试。"""

    def test_load_existing_template(self):
        """加载实际存在的模板文件。"""
        content = load_skill_template("generate-character")
        assert len(content) > 0
        assert "模板文件未找到" not in content

    def test_load_nonexistent_template(self):
        """加载不存在的模板返回提示信息。"""
        # 清除缓存
        from novel_eval.tasks.skill_generation import judge as judge_mod
        judge_mod._SKILL_CACHE.pop("generate-nonexistent", None)

        content = load_skill_template("generate-nonexistent")
        assert "模板文件未找到" in content



# ------------------------------------------------------------------
# 6.3 _prepare_work_dir 测试
# ------------------------------------------------------------------


class TestPrepareWorkDir:
    """work_dir 清理策略测试。"""

    def test_preserve_setting_dirs(self, tmp_path: Path):
        """保留设定目录，清空其中文件。"""
        from novel_eval.tasks.skill_generation.task import SkillGenerationTask, _SETTING_DIRS

        # 创建 work_dir 结构
        for d in _SETTING_DIRS:
            dir_path = tmp_path / d
            dir_path.mkdir()
            (dir_path / "test.md").write_text("内容", encoding="utf-8")

        # 创建 mock task
        with patch("novel_eval.tasks.skill_generation.task.EvalRunner"):
            from novel_eval.tasks.tool_usage.models import EvalConfig
            config = EvalConfig(runner={"work_dir": str(tmp_path)})
            task = SkillGenerationTask(config)
            task.runner.work_dir = tmp_path

            task._prepare_work_dir()

        # 设定目录应保留
        for d in _SETTING_DIRS:
            assert (tmp_path / d).exists()
            assert (tmp_path / d).is_dir()
            # 其中文件应被清空
            assert not (tmp_path / d / "test.md").exists()

    def test_delete_non_setting_dirs(self, tmp_path: Path):
        """删除非设定目录。"""
        from novel_eval.tasks.skill_generation.task import SkillGenerationTask

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "data.json").write_text("{}", encoding="utf-8")

        with patch("novel_eval.tasks.skill_generation.task.EvalRunner"):
            from novel_eval.tasks.tool_usage.models import EvalConfig
            config = EvalConfig(runner={"work_dir": str(tmp_path)})
            task = SkillGenerationTask(config)
            task.runner.work_dir = tmp_path

            task._prepare_work_dir()

        # sessions 目录应被删除
        assert not sessions_dir.exists()

    def test_create_missing_dirs(self, tmp_path: Path):
        """设定目录不存在时自动创建。"""
        from novel_eval.tasks.skill_generation.task import SkillGenerationTask, _SETTING_DIRS

        work_dir = tmp_path / "new_work_dir"
        # 不创建任何子目录

        with patch("novel_eval.tasks.skill_generation.task.EvalRunner"):
            from novel_eval.tasks.tool_usage.models import EvalConfig
            config = EvalConfig(runner={"work_dir": str(work_dir)})
            task = SkillGenerationTask(config)
            task.runner.work_dir = work_dir

            task._prepare_work_dir()

        for d in _SETTING_DIRS:
            assert (work_dir / d).exists()


# ------------------------------------------------------------------
# 6.4 Task 集成测试
# ------------------------------------------------------------------


class TestSkillGenerationTaskInit:
    """Task 初始化测试。"""

    def test_init_with_default_config(self):
        """默认配置初始化。"""
        from novel_eval.tasks.skill_generation.task import SkillGenerationTask

        with patch("novel_eval.tasks.skill_generation.task.EvalRunner"):
            task = SkillGenerationTask()
            assert task.name == "skill_generation"
            assert task.runner is not None

    def test_init_with_config(self):
        """使用自定义配置初始化。"""
        from novel_eval.tasks.skill_generation.task import SkillGenerationTask
        from novel_eval.tasks.tool_usage.models import EvalConfig

        config = EvalConfig(
            runner={"work_dir": "/tmp/test", "agent_file": "agents/test/agent.yaml"},
            tasks={
                "skill_generation": {
                    "data_dir": "data/test",
                    "min_descriptions": 10,
                    "samples_per_type": 5,
                },
            },
        )

        with patch("novel_eval.tasks.skill_generation.task.EvalRunner"):
            task = SkillGenerationTask(config)
            assert task.eval_config.tasks.get("skill_generation") is not None


class TestTaskIntegration:
    """Task 完整流程 mock 测试。"""

    async def test_evaluate_with_mock(self, sample_csv: Path, tmp_path: Path):
        """mock runner 和 judge 的完整评估流程。"""
        from novel_eval.tasks.skill_generation.task import SkillGenerationTask
        from novel_eval.tasks.tool_usage.models import EvalConfig

        config = EvalConfig(
            runner={"work_dir": str(tmp_path / "work"), "agent_file": "agents/test/agent.yaml"},
            tasks={
                "skill_generation": {
                    "data_dir": str(sample_csv.parent.parent),
                    "min_descriptions": 20,
                    "samples_per_type": 5,
                },
            },
        )

        with patch("novel_eval.tasks.skill_generation.task.EvalRunner") as MockRunner:
            # mock runner
            mock_runner = MagicMock()
            mock_runner.work_dir = tmp_path / "work"
            mock_runner.run_case.return_value = CaseResult(
                case_id="SG-CHAR-001",
                question="测试问题",
                tool_calls=[ToolCallRecord(step=1, tool_name="SearchEntity", params={}, result_summary="结果")],
                final_answer="回答",
            )
            MockRunner.return_value = mock_runner

            task = SkillGenerationTask(config)

            # 生成用例
            cases = task.generate_cases("凡人修仙传", seed=42)
            assert len(cases) > 0

            # mock judge
            task._judge_case_sync = MagicMock(return_value=EvalScore(
                case_id="SG-CHAR-001",
                scores={
                    "tool_selection": 4,
                    "param_quality": 3,
                    "call_efficiency": 5,
                    "setting_completeness": 4,
                    "format_compliance": 5,
                },
                total_score=4.2,
                commentary="测试评语",
            ))

            # 设置 work_dir
            (tmp_path / "work").mkdir(exist_ok=True)

            # 执行评估
            results = await task.evaluate(cases)
            assert len(results) > 0

            for case_result, eval_score in results:
                assert isinstance(case_result, CaseResult)
                assert isinstance(eval_score, EvalScore)
