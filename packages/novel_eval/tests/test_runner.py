"""EvalRunner 测试模块。

测试 EvalRunner 的核心功能：
1. 命令构建（含 --model 参数）
2. session 隔离（清除旧 session）
3. wire.jsonl 解析
4. 超时处理
5. 批量执行并发控制
"""

import asyncio
import json
import subprocess
from hashlib import md5
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 直接从具体模块导入，避免通过 novel_eval.__init__ 导入造成的循环导入
from novel_eval.models import CaseResult, EvalCase, ToolCallRecord
from novel_eval.tasks.tool_usage.models import EvalConfig, RunnerConfig

# runner 模块需要单独导入，因为它依赖 novel_cli.share
import novel_eval.runner as runner_module

EvalRunner = runner_module.EvalRunner
resolve_sessions_dir = runner_module.resolve_sessions_dir


class TestResolveSessionsDir:
    """测试 sessions 目录解析。"""

    def test_resolve_sessions_dir_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 md5 hash 路径解析。"""
        # 设置环境变量以避免使用真实 home 目录
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        work_dir = tmp_path / "test_work"
        sessions_dir = resolve_sessions_dir(work_dir)

        # 验证路径格式
        assert ".novel" in str(sessions_dir)
        assert "sessions" in str(sessions_dir)

        # 验证 md5 hash
        expected_hash = md5(str(work_dir.expanduser().resolve()).encode()).hexdigest()
        assert sessions_dir.name == expected_hash

    def test_resolve_sessions_dir_respects_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试环境变量 NOVEL_SHARE_DIR 被正确使用。"""
        custom_share_dir = tmp_path / "custom_novel"
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(custom_share_dir))

        work_dir = tmp_path / "work"
        sessions_dir = resolve_sessions_dir(work_dir)

        assert custom_share_dir in sessions_dir.parents


class TestEvalRunnerInit:
    """测试 EvalRunner 初始化。"""

    def test_init_with_default_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试使用默认配置初始化。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)

        assert runner.config == config
        assert runner.timeout == 60
        assert runner.work_dir is not None

    def test_init_with_custom_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试自定义超时时间。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig(runner=RunnerConfig(timeout=120))
        runner = EvalRunner(config)

        assert runner.timeout == 120

    def test_init_with_custom_work_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试自定义工作目录。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        work_dir = tmp_path / "custom_work"
        config = EvalConfig(runner=RunnerConfig(work_dir=str(work_dir)))
        runner = EvalRunner(config)

        assert runner.work_dir == work_dir


class TestBuildCommand:
    """测试命令构建。"""

    def test_build_command_with_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试包含 --model 参数。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(
            id="L1-001",
            book="凡人修仙传",
            question="韩立是谁？",
            model="deepseek-v3",
        )

        cmd = runner._build_command(case)

        assert "novel-cli" in cmd
        assert "--print" in cmd
        assert "--model" in cmd
        assert "deepseek-v3" in cmd
        assert "--book" in cmd
        assert "凡人修仙传" in cmd
        assert "-p" in cmd
        assert "韩立是谁？" in cmd

    def test_build_command_without_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试不含 --model 参数（model 为 None）。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(
            id="L1-002",
            book="凡人修仙传",
            question="黄枫谷在哪？",
            model=None,
        )

        cmd = runner._build_command(case)

        assert "--model" not in cmd

    def test_build_command_session_id_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 session ID 格式。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="TEST-001", book="测试书", question="测试问题")

        cmd = runner._build_command(case)

        assert "--session" in cmd
        session_idx = cmd.index("--session")
        assert cmd[session_idx + 1] == "eval-TEST-001"

    def test_build_command_with_custom_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试自定义 agent 参数。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig(runner=RunnerConfig(agent_file="custom-agent.yaml"))
        runner = EvalRunner(config)
        case = EvalCase(id="TEST-001", book="测试书", question="测试问题")

        cmd = runner._build_command(case)

        assert "--agent-file" in cmd
        agent_file_idx = cmd.index("--agent-file")
        assert cmd[agent_file_idx + 1] == "custom-agent.yaml"

    def test_build_command_with_work_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 work_dir 参数。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        work_dir = tmp_path / "my_work"
        config = EvalConfig(runner=RunnerConfig(work_dir=str(work_dir)))
        runner = EvalRunner(config)
        case = EvalCase(id="TEST-001", book="测试书", question="测试问题")

        cmd = runner._build_command(case)

        assert "--work-dir" in cmd
        work_dir_idx = cmd.index("--work-dir")
        assert cmd[work_dir_idx + 1] == str(work_dir)


class TestPrepareSession:
    """测试 session 准备。"""

    def test_prepare_session_creates_work_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试创建工作目录。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        work_dir = tmp_path / "work"
        config = EvalConfig(runner=RunnerConfig(work_dir=str(work_dir)))
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        session_id = runner._prepare_session(case)

        assert runner.work_dir.exists()
        assert session_id == "eval-L1-001"

    def test_prepare_session_clears_old_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试清除旧 session 目录。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True)

        config = EvalConfig(runner=RunnerConfig(work_dir=str(work_dir)))
        runner = EvalRunner(config)

        # 创建旧 session 目录
        old_session_dir = runner.sessions_dir / "eval-L1-001"
        old_session_dir.mkdir(parents=True)
        old_file = old_session_dir / "old_context.jsonl"
        old_file.write_text("old data")

        case = EvalCase(id="L1-001", book="测试", question="测试问题")
        runner._prepare_session(case)

        # 旧目录应被清除
        assert not old_file.exists()
        assert not old_session_dir.exists()

    def test_prepare_session_no_old_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试无旧 session 时正常创建。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        work_dir = tmp_path / "work"
        config = EvalConfig(runner=RunnerConfig(work_dir=str(work_dir)))
        runner = EvalRunner(config)
        case = EvalCase(id="L1-NEW", book="测试", question="测试问题")

        # 无旧 session，不应报错
        session_id = runner._prepare_session(case)

        assert session_id == "eval-L1-NEW"

    def test_prepare_session_clears_work_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试每次执行前清空 work_dir 内容。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        # 模拟上一次执行残留的文件
        (work_dir / "cache.dat").write_text("old cache")
        (work_dir / "subdir").mkdir()
        (work_dir / "subdir" / "data.txt").write_text("old data")

        config = EvalConfig(runner=RunnerConfig(work_dir=str(work_dir)))
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        runner._prepare_session(case)

        # work_dir 目录本身存在，但内容被清空
        assert work_dir.exists()
        assert not (work_dir / "cache.dat").exists()
        assert not (work_dir / "subdir").exists()


class TestParseWire:
    """测试 wire.jsonl 解析。"""

    def test_parse_wire_tool_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试解析 ToolCall 消息。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        # 创建模拟 wire.jsonl（新模式：SearchEntity 工具名，无 action 参数）
        wire_content = json.dumps({
            "timestamp": 1234567890.0,
            "message": {
                "type": "ToolCall",
                "payload": {
                    "id": "call_001",
                    "function": {
                        "name": "SearchEntity",
                        "arguments": json.dumps({"query": "韩立"}),
                    },
                },
            },
        })
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text(wire_content + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert result.case_id == "L1-001"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "SearchEntity"
        assert result.tool_calls[0].params["query"] == "韩立"

    def test_parse_wire_tool_result(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试解析 ToolResult 消息。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        # 创建包含 ToolCall + ToolResult 的 wire.jsonl（新模式）
        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": json.dumps({"query": "韩立"}),
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolResult",
                    "payload": {
                        "tool_call_id": "call_001",
                        "return_value": {
                            "output": "韩立是男主角",
                        },
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert len(result.tool_calls) == 1
        assert "韩立是男主角" in result.tool_calls[0].result_summary

    def test_parse_wire_text_part(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试解析 TextPart 消息（最终回答）。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "TextPart",
                    "payload": {"text": "韩立是男主角，来自七玄门"},
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert result.final_answer == "韩立是男主角，来自七玄门"

    def test_parse_wire_content_part_text(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试解析 ContentPart (type=text) 消息（wire 协议新格式）。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ContentPart",
                    "payload": {"type": "text", "text": "韩立是男主角，来自七玄门"},
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert result.final_answer == "韩立是男主角，来自七玄门"

    def test_parse_wire_content_part_think_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 ContentPart (type=think) 不被当作最终回答。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ContentPart",
                    "payload": {"type": "think", "think": "思考过程"},
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ContentPart",
                    "payload": {"type": "text", "text": "最终回答"},
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert result.final_answer == "最终回答"

    def test_parse_wire_unknown_type_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试跳过未知消息类型。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "UnknownType",
                    "payload": {"data": "test"},
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": json.dumps({}),
                        },
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        # 未知类型被跳过，只有 ToolCall 被解析
        assert len(result.tool_calls) == 1

    def test_parse_wire_file_not_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 wire.jsonl 文件不存在。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        result = runner._parse_wire(tmp_path / "not_exists.jsonl", case, 1.0)

        assert result.error == "wire.jsonl 文件不存在"

    def test_parse_wire_multiple_tool_calls(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试解析多个 ToolCall 消息。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": json.dumps({"name": "韩立"}),
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_002",
                        "function": {
                            "name": "SearchGraph",
                            "arguments": json.dumps({"from": "韩立", "to": "南宫婉"}),
                        },
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].step == 1
        assert result.tool_calls[0].tool_name == "SearchEntity"
        assert result.tool_calls[1].step == 2
        assert result.tool_calls[1].tool_name == "SearchGraph"

    def test_parse_wire_result_truncation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 ToolResult 内容截断。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        # 创建一个超长的 ToolResult 内容
        long_content = "这是一段很长的内容。" * 100  # 约 1000 字符

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": json.dumps({}),
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolResult",
                    "payload": {
                        "tool_call_id": "call_001",
                        "return_value": {
                            "output": long_content,
                        },
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        # 结果不应被截断，保留完整内容
        assert result.tool_calls[0].result_summary == long_content

    def test_parse_wire_empty_arguments(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试空 arguments 参数解析。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "unknown_tool",  # 未知工具名
                            "arguments": "",  # 空 arguments
                        },
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        # 空 arguments 应被解析为空字典
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].params == {}
        assert result.tool_calls[0].tool_name == "unknown_tool"  # 保留原始工具名

    def test_parse_wire_toolcallpart_single(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 ToolCall + ToolCallPart 碎片拼接（单片段）。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": "{",
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": 1.1,
                "message": {
                    "type": "ToolCallPart",
                    "payload": {
                        "arguments_part": '"query": "韩立", "search_mode": "name"}',
                    },
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolResult",
                    "payload": {
                        "tool_call_id": "call_001",
                        "return_value": {"output": "找到韩立"},
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].params == {"query": "韩立", "search_mode": "name"}
        assert result.tool_calls[0].result_summary == "找到韩立"

    def test_parse_wire_toolcallpart_multiple(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 ToolCall + 多个 ToolCallPart 碎片拼接。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchGraph",
                            "arguments": "",
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": 1.1,
                "message": {
                    "type": "ToolCallPart",
                    "payload": {"arguments_part": '{"ent'},
                },
            }),
            json.dumps({
                "timestamp": 1.2,
                "message": {
                    "type": "ToolCallPart",
                    "payload": {"arguments_part": 'ity_id": "韩立"}'},
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolResult",
                    "payload": {
                        "tool_call_id": "call_001",
                        "return_value": {"output": "关系图"},
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].params == {"entity_id": "韩立"}
        assert result.tool_calls[0].result_summary == "关系图"

    def test_parse_wire_toolcallpart_with_parallel_calls(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试并行的 ToolCall 各自有 ToolCallPart 碎片时正确拼接。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            # 第一个 ToolCall（完整 arguments）
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": json.dumps({"query": "韩立"}),
                        },
                    },
                },
            }),
            # 第二个 ToolCall（不完整 arguments）
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_002",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": "{",
                        },
                    },
                },
            }),
            # 第一个 ToolCall 的 ToolResult（触发 flush）
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolResult",
                    "payload": {
                        "tool_call_id": "call_001",
                        "return_value": {"output": "找到韩立"},
                    },
                },
            }),
            # 第二个 ToolCall 的 ToolCallPart（属于 call_002，但只能追加到最后一个 ToolCall）
            json.dumps({
                "timestamp": 2.1,
                "message": {
                    "type": "ToolCallPart",
                    "payload": {"arguments_part": '"query": "南宫婉"}'},
                },
            }),
            # 第二个 ToolCall 的 ToolResult
            json.dumps({
                "timestamp": 3.0,
                "message": {
                    "type": "ToolResult",
                    "payload": {
                        "tool_call_id": "call_002",
                        "return_value": {"output": "找到南宫婉"},
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].params == {"query": "韩立"}
        assert result.tool_calls[0].result_summary == "找到韩立"
        assert result.tool_calls[1].params == {"query": "南宫婉"}
        assert result.tool_calls[1].result_summary == "找到南宫婉"

    def test_parse_wire_toolcallpart_no_part_backward_compat(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试无 ToolCallPart 时行为不变（向后兼容）。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": json.dumps({"query": "韩立"}),
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolResult",
                    "payload": {
                        "tool_call_id": "call_001",
                        "return_value": {"output": "结果"},
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].params == {"query": "韩立"}
        assert result.tool_calls[0].result_summary == "结果"

    def test_parse_wire_invalid_json_line(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试跳过无效 JSON 行。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig()
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        wire_lines = [
            "invalid json line",
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": json.dumps({}),
                        },
                    },
                },
            }),
        ]
        wire_path = tmp_path / "wire.jsonl"
        wire_path.write_text("\n".join(wire_lines) + "\n")

        result = runner._parse_wire(wire_path, case, 1.0)

        # 无效 JSON 行被跳过
        assert len(result.tool_calls) == 1


class TestRunCase:
    """测试执行单个用例。"""

    @patch("novel_eval.runner.subprocess.run")
    def test_run_case_timeout(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试超时处理。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="novel-cli", timeout=60)

        config = EvalConfig(runner=RunnerConfig(work_dir=str(tmp_path / "work"), timeout=60))
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        result = runner.run_case(case)

        assert result.error is not None
        assert "超时" in result.error

    @patch("novel_eval.runner.subprocess.run")
    def test_run_case_process_error(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试子进程执行失败。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd="novel-cli")

        config = EvalConfig(runner=RunnerConfig(work_dir=str(tmp_path / "work")))
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        result = runner.run_case(case)

        assert result.error is not None
        assert "子进程执行失败" in result.error

    @patch("novel_eval.runner.subprocess.run")
    def test_run_case_success(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试成功执行。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig(runner=RunnerConfig(work_dir=str(tmp_path / "work")))
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        # session_dir 需要在 _prepare_session 之后创建（它会被清除）
        session_dir = runner.sessions_dir / "eval-L1-001"
        wire_path = session_dir / "wire.jsonl"

        def create_wire_and_return(*args, **kwargs):
            """在 subprocess.run mock 被调用时创建 wire.jsonl。"""
            # _prepare_session 已清除目录，这里重新创建
            session_dir.mkdir(parents=True)
            wire_path.write_text(
                json.dumps({
                    "timestamp": 1.0,
                    "message": {
                        "type": "TextPart",
                        "payload": {"text": "测试回答"},
                    },
                }) + "\n"
            )
            return None

        mock_run.side_effect = create_wire_and_return

        result = runner.run_case(case)

        assert result.case_id == "L1-001"
        assert result.final_answer == "测试回答"
        assert result.error is None

    @patch("novel_eval.runner.subprocess.run")
    def test_run_case_with_tool_calls(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试执行包含工具调用的用例。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig(runner=RunnerConfig(work_dir=str(tmp_path / "work")))
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="韩立是谁？")

        session_dir = runner.sessions_dir / "eval-L1-001"
        wire_path = session_dir / "wire.jsonl"
        wire_lines = [
            json.dumps({
                "timestamp": 1.0,
                "message": {
                    "type": "ToolCall",
                    "payload": {
                        "id": "call_001",
                        "function": {
                            "name": "SearchEntity",
                            "arguments": json.dumps({"name": "韩立"}),
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": 2.0,
                "message": {
                    "type": "ToolResult",
                    "payload": {
                        "tool_call_id": "call_001",
                        "return_value": {
                            "output": "韩立，男主角，来自七玄门...",
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": 3.0,
                "message": {
                    "type": "TextPart",
                    "payload": {"text": "韩立是这部小说的男主角。"},
                },
            }),
        ]

        def create_wire_and_return(*args, **kwargs):
            """在 subprocess.run mock 被调用时创建 wire.jsonl。"""
            session_dir.mkdir(parents=True)
            wire_path.write_text("\n".join(wire_lines) + "\n")
            return None

        mock_run.side_effect = create_wire_and_return

        result = runner.run_case(case)

        assert result.case_id == "L1-001"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "SearchEntity"
        assert result.final_answer == "韩立是这部小说的男主角。"

    @patch("novel_eval.runner.subprocess.run")
    def test_run_case_exception(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试执行过程中抛出异常。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        mock_run.side_effect = RuntimeError("Unexpected error")

        config = EvalConfig(runner=RunnerConfig(work_dir=str(tmp_path / "work")))
        runner = EvalRunner(config)
        case = EvalCase(id="L1-001", book="测试", question="测试问题")

        result = runner.run_case(case)

        assert result.error is not None
        assert "执行异常" in result.error


class TestRunBatch:
    """测试批量执行。"""

    async def test_run_batch_concurrency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试并发控制。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig(concurrency=2, runner=RunnerConfig(work_dir=str(tmp_path / "work")))
        runner = EvalRunner(config)

        cases = [
            EvalCase(id="L1-001", book="测试", question="问题1"),
            EvalCase(id="L1-002", book="测试", question="问题2"),
        ]

        # Mock run_case
        with patch.object(
            runner,
            "run_case",
            return_value=CaseResult(
                case_id="mock",
                question="mock",
                tool_calls=[],
                final_answer="",
                execution_time=1.0,
            ),
        ):
            results = await runner.run_batch(cases)

        assert len(results) == 2

    async def test_run_batch_preserves_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试结果顺序与输入一致。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig(concurrency=1, runner=RunnerConfig(work_dir=str(tmp_path / "work")))
        runner = EvalRunner(config)

        cases = [
            EvalCase(id="L1-001", book="测试", question="问题1"),
            EvalCase(id="L1-002", book="测试", question="问题2"),
            EvalCase(id="L1-003", book="测试", question="问题3"),
        ]

        # Mock run_case to return case-specific results
        def mock_run_case(case: EvalCase) -> CaseResult:
            return CaseResult(
                case_id=case.id,
                question=case.question,
                tool_calls=[],
                final_answer=f"Answer for {case.id}",
                execution_time=0.1,
            )

        with patch.object(runner, "run_case", side_effect=mock_run_case):
            results = await runner.run_batch(cases)

        assert len(results) == 3
        assert results[0].case_id == "L1-001"
        assert results[1].case_id == "L1-002"
        assert results[2].case_id == "L1-003"

    async def test_run_batch_empty_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试空用例列表。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig(runner=RunnerConfig(work_dir=str(tmp_path / "work")))
        runner = EvalRunner(config)

        results = await runner.run_batch([])

        assert results == []

    async def test_run_batch_with_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试批量执行中有部分用例失败。"""
        monkeypatch.setenv("NOVEL_SHARE_DIR", str(tmp_path / ".novel"))

        config = EvalConfig(concurrency=2, runner=RunnerConfig(work_dir=str(tmp_path / "work")))
        runner = EvalRunner(config)

        cases = [
            EvalCase(id="L1-001", book="测试", question="问题1"),
            EvalCase(id="L1-002", book="测试", question="问题2"),
        ]

        call_count = 0

        def mock_run_case(case: EvalCase) -> CaseResult:
            nonlocal call_count
            call_count += 1
            if case.id == "L1-001":
                return CaseResult(
                    case_id=case.id,
                    question=case.question,
                    tool_calls=[],
                    final_answer="成功",
                    execution_time=0.1,
                )
            else:
                return CaseResult(
                    case_id=case.id,
                    question=case.question,
                    tool_calls=[],
                    final_answer="",
                    execution_time=0.1,
                    error="执行失败",
                )

        with patch.object(runner, "run_case", side_effect=mock_run_case):
            results = await runner.run_batch(cases)

        assert len(results) == 2
        assert results[0].error is None
        assert results[1].error == "执行失败"