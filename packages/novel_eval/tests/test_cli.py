"""CLI 集成测试。

测试 novel-eval 命令行接口的三个子命令：
- generate: 生成测试用例
- run: 执行评估
- regen-report: 从已有 cases 目录重新生成报告
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from novel_eval.cli import app

runner = CliRunner()


class TestCLIHelp:
    """CLI 帮助信息测试。"""

    def test_cli_help(self):
        """测试 CLI 主命令帮助信息。"""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "novel-eval" in result.output

    def test_generate_help(self):
        """测试 generate 命令帮助信息。"""
        result = runner.invoke(app, ["generate", "--help"])
        assert result.exit_code == 0
        assert "生成测试用例集" in result.output

    def test_run_help(self):
        """测试 run 命令帮助信息。"""
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "执行评估" in result.output

    def test_regen_report_help(self):
        """测试 regen-report 命令帮助信息。"""
        result = runner.invoke(app, ["regen-report", "--help"])
        assert result.exit_code == 0
        assert "重新生成报告" in result.output


class TestGenerateCommand:
    """generate 命令测试。"""

    @patch("novel_eval.cli.get_task_cls")
    def test_generate_default_params(self, mock_get_task_cls, tmp_path: Path):
        """测试 generate 命令使用默认参数。"""
        mock_task = MagicMock()
        mock_task.generate_cases.return_value = []
        mock_get_task_cls.return_value.return_value = mock_task
        result = runner.invoke(
            app, ["generate", "--output", str(tmp_path / "eval_cases")]
        )
        assert result.exit_code == 0
        assert "凡人修仙传" in result.output
        assert "level_1_entity" in result.output

    @patch("novel_eval.cli.get_task_cls")
    def test_generate_custom_params(self, mock_get_task_cls, tmp_path: Path):
        """测试 generate 命令使用自定义参数。"""
        mock_task = MagicMock()
        mock_task.generate_cases.return_value = []
        mock_get_task_cls.return_value.return_value = mock_task
        result = runner.invoke(
            app,
            ["generate", "--book", "斗破苍穹", "--scenario", "level_2_chain",
             "--output", str(tmp_path / "eval_cases")],
        )
        assert result.exit_code == 0
        assert "斗破苍穹" in result.output
        assert "level_2_chain" in result.output

    def test_generate_unknown_task(self):
        """测试 generate 命令使用未知任务类型。"""
        result = runner.invoke(app, ["generate", "--task", "unknown"])
        assert result.exit_code == 1
        assert "未知任务类型" in result.output


class TestRunCommand:
    """run 命令测试。"""

    def test_run_without_yaml(self):
        """测试 run 命令缺少 yaml 参数。"""
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 1
        assert "请指定 YAML" in result.output

    def test_run_yaml_not_exists(self):
        """测试 run 命令指定不存在的 yaml 文件。"""
        result = runner.invoke(
            app,
            ["run", "/nonexistent/path.yaml"],
        )
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_run_with_yaml(self, tmp_path: Path):
        """测试 run 命令指定有效的 yaml 文件。"""
        # 创建测试 YAML
        yaml_content = """
book: 凡人修仙传
level: 1
description: 测试用例集
cases:
  - id: L1-001
    question: 韩立是谁？
    book: 凡人修仙传
    meta:
      involved_entities:
        - 韩立
      expected_tool_types:
        - entity
"""
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(yaml_content, encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "run",
                str(yaml_path),
                "--output",
                str(tmp_path / "results"),
            ],
        )
        # 应该成功加载 YAML 并执行
        assert "加载了 1 个用例" in result.output or result.exit_code == 0

    def test_run_unknown_task(self, tmp_path: Path):
        """测试 run 命令使用未知任务类型。"""
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text("book: test\nlevel: 1\ncases: []\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["run", "--task", "unknown", str(yaml_path)],
        )
        assert result.exit_code == 1
        assert "未知任务类型" in result.output

    def test_skip_run_without_result_dir(self):
        """测试 skip-run 模式缺少 --result-dir。"""
        result = runner.invoke(app, ["run", "--skip-run"])
        assert result.exit_code == 1
        assert "必须指定 --result-dir" in result.output

    def test_rerun_missing_without_result_dir(self):
        """测试 rerun-missing 模式缺少 --result-dir。"""
        result = runner.invoke(app, ["run", "--rerun-missing"])
        assert result.exit_code == 1
        assert "必须指定 --result-dir" in result.output


class TestRegenReportCommand:
    """regen-report 命令测试。"""

    def test_regen_report_without_result_dir(self):
        """测试 regen-report 命令缺少 result-dir 参数。"""
        result = runner.invoke(app, ["regen-report"])
        assert result.exit_code != 0

    def test_regen_report_dir_not_exists(self):
        """测试 regen-report 命令指定不存在的目录。"""
        result = runner.invoke(
            app,
            ["regen-report", "--result-dir", "/nonexistent/path"],
        )
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_regen_report_no_cases_dir(self, tmp_path: Path):
        """测试 regen-report 命令目录中没有 cases 子目录。"""
        result = runner.invoke(
            app,
            ["regen-report", "--result-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "cases 目录不存在" in result.output

    def test_regen_report_empty_cases_dir(self, tmp_path: Path):
        """测试 regen-report 命令 cases 目录中没有 JSON 文件。"""
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        result = runner.invoke(
            app,
            ["regen-report", "--result-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "cases 目录中没有 JSON 文件" in result.output

    def test_regen_report_success(self, tmp_path: Path):
        """测试 regen-report 正常重新生成报告。"""
        # 构造 cases 目录和 JSON 文件
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        case_data = {
            "case_id": "L1-001",
            "question": "韩立是谁？",
            "tool_calls": [
                {"step": 1, "tool_name": "SearchEntity", "params": {"query": "韩立"}, "result_summary": "找到韩立"},
            ],
            "final_answer": "韩立是男主角",
            "execution_time": 1.0,
            "error": None,
            "score": {
                "scores": {"tool_selection": 4, "param_quality": 4, "call_efficiency": 4, "result_utilization": 4},
                "total_score": 4.0,
                "commentary": "表现优秀",
            },
        }
        case_file = cases_dir / "L1-001.json"
        case_file.write_text(json.dumps(case_data, ensure_ascii=False), encoding="utf-8")

        result = runner.invoke(
            app,
            ["regen-report", "--result-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "加载了 1 个用例结果" in result.output
        assert "报告已重新生成" in result.output
        assert (tmp_path / "report.json").exists()
        assert (tmp_path / "report.md").exists()

        # 验证 report.json 包含工具统计
        with open(tmp_path / "report.json", encoding="utf-8") as f:
            report = json.load(f)
        assert report["tool_stats"]["global"]["total_calls"] == 1
        assert "tool_stats" in report["cases_summary"][0]

    def test_regen_report_multiple_cases(self, tmp_path: Path):
        """测试 regen-report 处理多个用例。"""
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        for i in range(3):
            case_data = {
                "case_id": f"L1-{i + 1:03d}",
                "question": f"测试问题{i + 1}",
                "tool_calls": [
                    {"step": 1, "tool_name": "SearchEntity", "params": {"query": f"实体{i + 1}"}, "result_summary": f"结果{i + 1}"},
                ],
                "final_answer": f"回答{i + 1}",
                "execution_time": 1.0,
                "error": None,
                "score": {
                    "scores": {"tool_selection": 4, "param_quality": 4, "call_efficiency": 4, "result_utilization": 4},
                    "total_score": 4.0,
                    "commentary": "",
                },
            }
            (cases_dir / f"L1-{i + 1:03d}.json").write_text(
                json.dumps(case_data, ensure_ascii=False), encoding="utf-8"
            )

        result = runner.invoke(
            app,
            ["regen-report", "--result-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "加载了 3 个用例结果" in result.output


class TestExtractContextCommand:
    """extract-context 命令测试。"""

    def test_extract_context_help(self):
        """测试 extract-context 命令帮助信息。"""
        result = runner.invoke(app, ["extract-context", "--help"])
        assert result.exit_code == 0
        assert "提取上下文消息" in result.output

    def test_extract_context_result_dir_not_exists(self):
        """测试 extract-context 命令指定不存在的结果目录。"""
        result = runner.invoke(
            app,
            ["extract-context", "--result-dir", "/nonexistent/path"],
        )
        assert result.exit_code == 1
        assert "结果目录不存在" in result.output

    def test_extract_context_no_cases_dir(self, tmp_path: Path):
        """测试 extract-context 命令缺少 cases 目录。"""
        result = runner.invoke(
            app,
            ["extract-context", "--result-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "cases 目录不存在" in result.output

    def test_extract_context_no_json_files(self, tmp_path: Path):
        """测试 extract-context 命令 cases 目录为空。"""
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()

        result = runner.invoke(
            app,
            ["extract-context", "--result-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "没有 JSON 文件" in result.output

    @patch("novel_eval.cli.ContextExtractor")
    @patch("novel_eval.cli.load_config")
    def test_extract_context_success(self, mock_load_config, mock_extractor_class, tmp_path: Path):
        """测试 extract-context 命令成功执行。"""
        # 创建 cases 目录和示例 JSON
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        case_data = {
            "case_id": "L1-001",
            "question": "韩立是谁？",
            "tool_calls": [],
            "final_answer": "韩立是男主角",
            "execution_time": 1.0,
            "error": None,
            "score": {"scores": {}, "total_score": 4.0, "commentary": ""},
        }
        (cases_dir / "L1-001.json").write_text(json.dumps(case_data, ensure_ascii=False), encoding="utf-8")

        # Mock config
        from unittest.mock import MagicMock
        mock_config = MagicMock()
        mock_config.runner.work_dir = "~/novel_test"
        mock_load_config.return_value = mock_config

        # Mock extractor
        mock_extractor = MagicMock()
        mock_extractor.extract_batch.return_value = {"L1-001": True}
        mock_extractor_class.return_value = mock_extractor

        result = runner.invoke(
            app,
            ["extract-context", "--result-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "提取完成：成功 1/1" in result.output
        mock_extractor.extract_batch.assert_called_once()


class TestResolveYamlPaths:
    """_resolve_yaml_paths 函数测试。"""

    def test_single_file(self, tmp_path: Path):
        """测试普通单文件路径。"""
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text("book: test", encoding="utf-8")

        from novel_eval.cli import _resolve_yaml_paths
        result = _resolve_yaml_paths([yaml_path])
        assert result == [yaml_path]

    def test_single_file_not_exists(self):
        """测试不存在的单文件路径。"""
        result = runner.invoke(app, ["run", "/nonexistent/path.yaml"])
        assert result.exit_code == 1
        assert "不存在" in result.output

    def test_multiple_files(self, tmp_path: Path):
        """测试多文件路径（模拟 shell 展开 glob）。"""
        f1 = tmp_path / "a.yaml"
        f2 = tmp_path / "b.yaml"
        f1.write_text("book: test", encoding="utf-8")
        f2.write_text("book: test", encoding="utf-8")

        from novel_eval.cli import _resolve_yaml_paths
        result = _resolve_yaml_paths([f1, f2])
        assert result == [f1, f2]

    def test_multiple_files_partial_missing(self, tmp_path: Path):
        """测试多文件中部分不存在时报错。"""
        f1 = tmp_path / "a.yaml"
        f1.write_text("book: test", encoding="utf-8")

        from click.exceptions import Exit
        from novel_eval.cli import _resolve_yaml_paths
        with pytest.raises(Exit):
            _resolve_yaml_paths([f1, tmp_path / "missing.yaml"])


class TestIntegration:
    """CLI 集成测试。"""

    def test_full_workflow(self, tmp_path: Path):
        """测试完整工作流：generate -> run。"""
        # Step 1: generate（框架实现返回空列表）
        generate_result = runner.invoke(
            app,
            ["generate", "--book", "测试书籍", "--output", str(tmp_path / "cases")],
        )
        assert generate_result.exit_code == 0

        # Step 2: 手动创建 YAML（因为 generate 未实现）
        cases_dir = tmp_path / "cases" / "测试书籍"
        cases_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = cases_dir / "level_1_entity.yaml"
        yaml_content = """
book: 测试书籍
level: 1
description: Level 1 测试用例
cases:
  - id: L1-001
    question: 主角是谁？
    book: 测试书籍
    meta:
      involved_entities:
        - 主角
      expected_tool_types:
        - entity
"""
        yaml_path.write_text(yaml_content, encoding="utf-8")

        # Step 3: run
        run_result = runner.invoke(
            app,
            [
                "run",
                str(yaml_path),
                "--output",
                str(tmp_path / "results"),
            ],
        )
        assert "加载了 1 个用例" in run_result.output
