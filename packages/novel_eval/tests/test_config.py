"""配置加载模块测试。"""

import os
from pathlib import Path
from unittest import mock

import pytest
import yaml

from novel_eval.config import load_config
from novel_eval.tasks.tool_usage.models import EvalConfig


class TestLoadConfig:
    """配置加载测试类。"""

    def test_load_config_with_complete_file(self, tmp_path: Path) -> None:
        """测试正常加载：有完整配置文件时正确解析。"""
        config_file = tmp_path / "config.yaml"
        config_content = """
agent:
  model: test-model
  base_url: https://api.test.com
  api_key_env: TEST_API_KEY

runner:
  work_dir: /tmp/test_work
  agent_file: test-agent
  timeout: 120

judge:
  model: test-judge-model
  api_key_env: TEST_JUDGE_KEY

settings_dir: /tmp/settings
concurrency: 4

tasks:
  tool_usage:
    books:
      - name: 测试书籍
        scenarios: [level_1_entity, level_2_chain]
"""
        config_file.write_text(config_content, encoding="utf-8")

        # 设置环境变量
        with mock.patch.dict(os.environ, {"TEST_API_KEY": "test-api-key", "TEST_JUDGE_KEY": "test-judge-key"}):
            config = load_config(config_file)

        assert isinstance(config, EvalConfig)
        assert config.agent.model == "test-model"
        assert config.agent.base_url == "https://api.test.com"
        assert config.agent.api_key_env == "TEST_API_KEY"
        assert config.agent.api_key == "test-api-key"
        assert config.runner.work_dir == "/tmp/test_work"
        assert config.runner.agent_file == "test-agent"
        assert config.runner.timeout == 120
        assert config.judge.model == "test-judge-model"
        assert config.judge.api_key == "test-judge-key"
        assert config.settings_dir == "/tmp/settings"
        assert config.concurrency == 4
        assert "tool_usage" in config.tasks

    def test_load_config_with_missing_fields(self, tmp_path: Path) -> None:
        """测试缺失字段默认值：部分字段缺失时使用默认值。"""
        config_file = tmp_path / "config.yaml"
        # 只提供部分配置
        config_content = """
agent:
  model: partial-model
"""
        config_file.write_text(config_content, encoding="utf-8")

        config = load_config(config_file)

        # 验证提供的值
        assert config.agent.model == "partial-model"
        # 验证默认值
        assert config.agent.base_url is None
        assert config.agent.api_key_env == "AGENT_API_KEY"
        assert config.agent.api_key == ""
        assert config.runner.work_dir == "~/novel_test"
        assert config.runner.agent_file == "agents/novel/agent.yaml"
        assert config.runner.timeout == 60
        assert config.judge.model == "claude-sonnet-4-20250514"
        assert config.settings_dir == "/github/novel2settings/settings"
        assert config.concurrency == 2

    def test_load_config_with_env_var_replacement(self, tmp_path: Path) -> None:
        """测试环境变量替换：api_key_env 字段正确读取环境变量。"""
        config_file = tmp_path / "config.yaml"
        config_content = """
agent:
  api_key_env: CUSTOM_AGENT_KEY

judge:
  api_key_env: CUSTOM_JUDGE_KEY
"""
        config_file.write_text(config_content, encoding="utf-8")

        # 设置自定义环境变量
        with mock.patch.dict(
            os.environ,
            {"CUSTOM_AGENT_KEY": "my-agent-key-123", "CUSTOM_JUDGE_KEY": "my-judge-key-456"},
        ):
            config = load_config(config_file)

        assert config.agent.api_key == "my-agent-key-123"
        assert config.judge.api_key == "my-judge-key-456"

    def test_load_config_with_missing_env_var(self, tmp_path: Path) -> None:
        """测试环境变量缺失时返回空字符串。"""
        config_file = tmp_path / "config.yaml"
        config_content = """
agent:
  api_key_env: NONEXISTENT_KEY
"""
        config_file.write_text(config_content, encoding="utf-8")

        # 确保环境变量不存在
        with mock.patch.dict(os.environ, {}, clear=True):
            # 重新加载 os 模块以确保环境变量被清除
            if "NONEXISTENT_KEY" in os.environ:
                del os.environ["NONEXISTENT_KEY"]
            config = load_config(config_file)

        assert config.agent.api_key == ""

    def test_load_config_with_nonexistent_file(self, tmp_path: Path) -> None:
        """测试配置文件不存在时返回默认配置。"""
        nonexistent_path = tmp_path / "nonexistent.yaml"
        config = load_config(nonexistent_path)

        assert isinstance(config, EvalConfig)
        # 验证所有默认值
        assert config.agent.model == "deepseek-v3"
        assert config.runner.work_dir == "~/novel_test"
        assert config.judge.model == "claude-sonnet-4-20250514"
        assert config.concurrency == 2

    def test_load_config_with_none_path(self) -> None:
        """测试 path=None 时加载默认配置文件。"""
        # 不传 path 参数，使用默认配置文件
        config = load_config(None)

        assert isinstance(config, EvalConfig)
        # 默认配置文件应该存在且有配置值
        # 这里只验证能正常加载，具体值取决于实际 config.yaml

    def test_load_config_with_empty_file(self, tmp_path: Path) -> None:
        """测试空配置文件返回默认配置。"""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("", encoding="utf-8")

        config = load_config(config_file)

        assert isinstance(config, EvalConfig)
        assert config.agent.model == "deepseek-v3"
        assert config.concurrency == 2