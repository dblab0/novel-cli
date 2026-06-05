"""Tests for setup 模块重构。

测试新架构下各模块的导入、数据流和核心逻辑。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from novel_cli.config import (
    Config,
    LLMModel,
    LLMProvider,
    load_config,
    save_config,
)
from novel_cli.llm import ALL_MODEL_CAPABILITIES, ModelCapability
from novel_cli.setup.components import mask_api_key
from novel_cli.setup.model import prompt_capabilities
from novel_cli.setup_providers import get_provider_def_by_id


# ─── 导入测试 ───


class TestImports:
    """验证所有新模块可正常导入。"""

    def test_setup_init(self) -> None:
        from novel_cli.setup import run_setup

        assert callable(run_setup)

    def test_components(self) -> None:
        from novel_cli.setup.components import (
            mask_api_key,
            prompt_choice,
            prompt_confirm,
            prompt_text,
            show_config_section,
        )

        assert callable(mask_api_key)
        assert callable(prompt_choice)
        assert callable(prompt_confirm)
        assert callable(prompt_text)

    def test_provider(self) -> None:
        from novel_cli.setup.provider import run_provider_menu

        assert callable(run_provider_menu)

    def test_model(self) -> None:
        from novel_cli.setup.model import run_model_menu

        assert callable(run_model_menu)

    def test_prefs(self) -> None:
        from novel_cli.setup.prefs import run_prefs_menu

        assert callable(run_prefs_menu)

    def test_services(self) -> None:
        from novel_cli.setup.services import run_services_menu

        assert callable(run_services_menu)

    def test_wizard(self) -> None:
        from novel_cli.setup.wizard import run_wizard

        assert callable(run_wizard)


# ─── 组件测试 ───


class TestMaskApiKey:
    """API Key 脱敏测试。"""

    def test_long_key(self) -> None:
        key = SecretStr("sk-awZpHXYkkyC2MCXsimSBtcPt8oreOqO7ITOImqU21b32aP2v")
        masked = mask_api_key(key)
        assert masked.startswith("sk-")
        assert masked.endswith("P2v")
        assert "***" in masked

    def test_short_key(self) -> None:
        masked = mask_api_key(SecretStr("abc"))
        assert masked == "***"

    def test_empty_key(self) -> None:
        masked = mask_api_key(SecretStr(""))
        assert masked == "(空)"

    def test_raw_string(self) -> None:
        masked = mask_api_key("sk-1234567890abcdef")
        assert masked.startswith("sk-")
        assert "***" in masked


# ─── Capabilities 测试 ───


class TestCapabilities:
    """能力选择逻辑测试。"""

    def test_all_capabilities_defined(self) -> None:
        assert "thinking" in ALL_MODEL_CAPABILITIES
        assert "image_in" in ALL_MODEL_CAPABILITIES
        assert "video_in" in ALL_MODEL_CAPABILITIES
        assert "always_thinking" in ALL_MODEL_CAPABILITIES

    async def test_accept_default_capabilities(self) -> None:
        with patch(
            "novel_cli.setup.model.prompt_choice",
            new_callable=AsyncMock,
            return_value="done",
        ):
            result = await prompt_capabilities()
            assert result is not None
            assert "thinking" in result

    async def test_cancel_capabilities(self) -> None:
        with patch(
            "novel_cli.setup.model.prompt_choice",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await prompt_capabilities()
            assert result is None

    async def test_thinking_always_thinking_mutex(self) -> None:
        """thinking 和 always_thinking 互斥。"""
        calls = iter(["always_thinking", "done"])
        with patch(
            "novel_cli.setup.model.prompt_choice",
            new_callable=AsyncMock,
            side_effect=lambda **kwargs: next(calls),
        ):
            result = await prompt_capabilities(default_capabilities={"thinking"})
            assert result is not None
            assert "always_thinking" in result
            assert "thinking" not in result

    async def test_switch_back_to_thinking(self) -> None:
        """从 always_thinking 切换回 thinking。"""
        calls = iter(["thinking", "done"])
        with patch(
            "novel_cli.setup.model.prompt_choice",
            new_callable=AsyncMock,
            side_effect=lambda **kwargs: next(calls),
        ):
            result = await prompt_capabilities(default_capabilities={"always_thinking"})
            assert result is not None
            assert "thinking" in result
            assert "always_thinking" not in result


# ─── 配置持久化测试 ───


class TestConfigPersistence:
    """配置读写测试。"""

    def test_save_and_load_model(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"

        cfg = Config(
            default_model="test/glm-5",
            providers={
                "test": LLMProvider(
                    type="anthropic",
                    base_url="http://localhost:4000",
                    api_key=SecretStr("sk-test"),
                ),
            },
            models={
                "test/glm-5": LLMModel(
                    provider="test",
                    model="glm-5",
                    max_context_size=200000,
                    capabilities={"thinking"},
                ),
            },
        )
        save_config(cfg, config_file)

        loaded = load_config(config_file)
        assert "test/glm-5" in loaded.models
        assert loaded.models["test/glm-5"].max_context_size == 200000
        assert "thinking" in loaded.models["test/glm-5"].capabilities

    def test_save_model_without_capabilities(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"

        cfg = Config(
            default_model="test/glm-5",
            providers={
                "test": LLMProvider(
                    type="anthropic",
                    base_url="http://localhost:4000",
                    api_key=SecretStr("sk-test"),
                ),
            },
            models={
                "test/glm-5": LLMModel(
                    provider="test",
                    model="glm-5",
                    max_context_size=200000,
                ),
            },
        )
        save_config(cfg, config_file)

        loaded = load_config(config_file)
        assert loaded.models["test/glm-5"].capabilities is None


# ─── Provider 定义测试 ───


class TestProviderDefs:
    """内置 Provider 定义测试。"""

    def test_get_provider_by_id(self) -> None:
        zhipu = get_provider_def_by_id("zhipu")
        assert zhipu is not None
        assert zhipu.display_name == "智谱 AI"

    def test_get_unknown_provider(self) -> None:
        unknown = get_provider_def_by_id("nonexistent")
        assert unknown is None
