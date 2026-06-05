"""YAML 读写单测。

测试 generator 模块中的 save_cases_yaml 和 load_cases_yaml 函数。
"""

import tempfile
from pathlib import Path

import pytest

from novel_eval.models import EvalCase
from novel_eval.tasks.tool_usage.generator import load_cases_yaml, save_cases_yaml


class TestSaveCasesYaml:
    """测试 save_cases_yaml 函数。"""

    def test_save_cases_yaml_creates_directory_structure(self, tmp_path: Path) -> None:
        """测试保存 YAML 时创建正确的目录结构。"""
        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",

                question="韩立是谁？",
                meta={"involved_entities": ["韩立"], "expected_tool_types": ["entity"]},
                validation={"entity_hits": 10, "top_hit": "韩立，男主角..."},
            ),
        ]

        base_dir = tmp_path / "eval_cases"
        save_cases_yaml(base_dir, cases, "凡人修仙传", scenario="level_1_entity")

        # 验证目录结构
        book_dir = base_dir / "凡人修仙传"
        assert book_dir.exists()
        assert book_dir.is_dir()

        # 验证文件存在
        yaml_file = book_dir / "level_1_entity.yaml"
        assert yaml_file.exists()

    def test_save_cases_yaml_level_filenames(self, tmp_path: Path) -> None:
        """测试不同难度等级对应不同的文件名。"""
        cases = [EvalCase(id="test", book="test_book", question="test")]

        base_dir = tmp_path / "eval_cases"
        book = "test_book"

        # 测试各场景的文件名
        scenario_names = {
            "level_1_entity": "level_1_entity.yaml",
            "level_2_chain": "level_2_chain.yaml",
            "level_3_multi_step": "level_3_multi_step.yaml",
            "level_4_complex": "level_4_complex.yaml",
        }

        for scenario, filename in scenario_names.items():
            save_cases_yaml(base_dir, cases, book, scenario=scenario)
            yaml_file = base_dir / book / filename
            assert yaml_file.exists(), f"场景 {scenario} 应创建 {filename}"

    def test_save_cases_yaml_content_structure(self, tmp_path: Path) -> None:
        """测试 YAML 文件内容结构正确。"""
        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",

                question="韩立是谁？",
                meta={"involved_entities": ["韩立"]},
                validation={"entity_hits": 10},
            ),
            EvalCase(
                id="L1-002",
                book="凡人修仙传",

                question="南宫婉是谁？",
                meta={"involved_entities": ["南宫婉"]},
                validation=None,
            ),
        ]

        base_dir = tmp_path / "eval_cases"
        save_cases_yaml(base_dir, cases, "凡人修仙传", scenario="level_1_entity")

        # 读取并验证内容
        import yaml

        yaml_file = base_dir / "凡人修仙传" / "level_1_entity.yaml"
        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert data["book"] == "凡人修仙传"
        assert data["level"] == 1
        assert data["description"] == "Level 1 测试用例"
        assert len(data["cases"]) == 2

        # 验证第一个用例
        case1 = data["cases"][0]
        assert case1["id"] == "L1-001"
        assert case1["question"] == "韩立是谁？"
        assert case1["meta"] == {"involved_entities": ["韩立"]}
        assert case1["_validation"] == {"entity_hits": 10}

    def test_save_cases_yaml_unicode_content(self, tmp_path: Path) -> None:
        """测试 Unicode 内容正确保存。"""
        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",

                question="韩立的修炼功法是什么？",
                meta={"entities": ["韩立", "长春功"]},
            ),
        ]

        base_dir = tmp_path / "eval_cases"
        save_cases_yaml(base_dir, cases, "凡人修仙传", scenario="level_1_entity")

        # 验证文件可以正确读取中文
        yaml_file = base_dir / "凡人修仙传" / "level_1_entity.yaml"
        content = yaml_file.read_text(encoding="utf-8")
        assert "韩立" in content
        assert "修炼功法" in content


class TestLoadCasesYaml:
    """测试 load_cases_yaml 函数。"""

    def test_load_cases_yaml_basic(self, tmp_path: Path) -> None:
        """测试基本的 YAML 加载功能。"""
        # 创建测试 YAML 文件
        yaml_content = """
book: 凡人修仙传
level: 1
description: Level 1 测试用例
cases:
  - id: L1-001
    model: deepseek-v3
    question: 韩立是谁？
    meta:
      involved_entities:
        - 韩立
    _validation:
      entity_hits: 10
"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        cases = load_cases_yaml(yaml_file)

        assert len(cases) == 1
        assert cases[0].id == "L1-001"
        assert cases[0].book == "凡人修仙传"
        assert cases[0].question == "韩立是谁？"
        assert cases[0].meta == {"involved_entities": ["韩立"]}
        assert cases[0].validation == {"entity_hits": 10}

    def test_load_cases_yaml_multiple_cases(self, tmp_path: Path) -> None:
        """测试加载多个用例。"""
        yaml_content = """
book: 凡人修仙传
level: 1
description: Level 1 测试用例
cases:
  - id: L1-001
    model: deepseek-v3
    question: 韩立是谁？
    meta: {}
  - id: L1-002
    model: deepseek-v3
    question: 南宫婉是谁？
    meta: {}
  - id: L1-003
    model: deepseek-v3
    question: 厉飞雨是谁？
    meta: {}
"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        cases = load_cases_yaml(yaml_file)

        assert len(cases) == 3
        assert cases[0].id == "L1-001"
        assert cases[1].id == "L1-002"
        assert cases[2].id == "L1-003"

    def test_load_cases_yaml_empty_cases(self, tmp_path: Path) -> None:
        """测试空用例列表。"""
        yaml_content = """
book: 凡人修仙传
level: 1
description: Level 1 测试用例
cases: []
"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        cases = load_cases_yaml(yaml_file)
        assert cases == []

    def test_load_cases_yaml_missing_optional_fields(self, tmp_path: Path) -> None:
        """测试缺失可选字段时使用默认值。"""
        yaml_content = """
book: 凡人修仙传
level: 1
description: Level 1 测试用例
cases:
  - id: L1-001
    question: 韩立是谁？
"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        cases = load_cases_yaml(yaml_file)

        assert len(cases) == 1
        assert cases[0].meta == {}
        assert cases[0].validation is None


class TestYamlRoundTrip:
    """测试 YAML 写入后读取的一致性。"""

    def test_round_trip_preserves_data(self, tmp_path: Path) -> None:
        """测试写入后读取能还原原始数据。"""
        original_cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",

                question="韩立是谁？",
                meta={"involved_entities": ["韩立"], "expected_tool_types": ["entity"]},
                validation={"entity_hits": 10, "top_hit": "韩立，男主角..."},
            ),
            EvalCase(
                id="L1-002",
                book="凡人修仙传",

                question="南宫婉和韩立的关系？",
                meta={"involved_entities": ["南宫婉", "韩立"], "expected_tool_types": ["graph"]},
                validation=None,
            ),
        ]

        base_dir = tmp_path / "eval_cases"
        save_cases_yaml(base_dir, original_cases, "凡人修仙传", scenario="level_1_entity")

        yaml_file = base_dir / "凡人修仙传" / "level_1_entity.yaml"
        loaded_cases = load_cases_yaml(yaml_file)

        assert len(loaded_cases) == len(original_cases)

        for original, loaded in zip(original_cases, loaded_cases):
            assert loaded.id == original.id
            assert loaded.book == original.book
            assert loaded.question == original.question
            assert loaded.meta == original.meta
            assert loaded.validation == original.validation