"""平台认证与模型管理模块。

提供平台定义、模型列表刷新和托管 provider 管理。
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple, cast

import aiohttp
from pydantic import BaseModel

from novel_cli.auth import NOVEL_CODE_PLATFORM_ID
from novel_cli.config import Config, LLMModel, load_config, save_config
from novel_cli.llm import ModelCapability
from novel_cli.utils.aiohttp import new_client_session
from novel_cli.utils.logging import logger


class ModelInfo(BaseModel):
    """API 返回的模型信息。

    Attributes:
        id: 模型标识符。
        context_length: 上下文长度。
        supports_reasoning: 是否支持推理。
        supports_image_in: 是否支持图像输入。
        supports_video_in: 是否支持视频输入。
    """

    id: str
    context_length: int
    supports_reasoning: bool
    supports_image_in: bool
    supports_video_in: bool

    @property
    def capabilities(self) -> set[ModelCapability]:
        """从模型信息推导能力集合。"""
        caps: set[ModelCapability] = set()
        if self.supports_reasoning:
            caps.add("thinking")
        # 名称包含 "thinking" 的模型为 always-thinking 类型
        if "thinking" in self.id.lower():
            caps.update(("thinking", "always_thinking"))
        if self.supports_image_in:
            caps.add("image_in")
        if self.supports_video_in:
            caps.add("video_in")
        # kimi-k2.5 系列模型支持多种能力
        if "kimi-k2.5" in self.id.lower():
            caps.update(("thinking", "image_in", "video_in"))
        return caps


class Platform(NamedTuple):
    """平台定义元组。

    Attributes:
        id: 平台标识符。
        name: 平台名称。
        base_url: API 基础 URL。
        search_url: 搜索 URL，可选。
        fetch_url: 获取 URL，可选。
        allowed_prefixes: 允许的模型前缀列表，可选。
    """

    id: str
    name: str
    base_url: str
    search_url: str | None = None
    fetch_url: str | None = None
    allowed_prefixes: list[str] | None = None


def _novel_code_base_url() -> str:
    """获取 Novel Code API 基础 URL，支持环境变量覆盖。"""
    if base_url := os.getenv("NOVEL_CODE_BASE_URL"):
        return base_url
    return "https://api.kimi.com/coding/v1"


# 平台列表定义
PLATFORMS: list[Platform] = [
    Platform(
        id=NOVEL_CODE_PLATFORM_ID,
        name="Novel Code",
        base_url=_novel_code_base_url(),
        search_url=f"{_novel_code_base_url()}/search",
        fetch_url=f"{_novel_code_base_url()}/fetch",
    ),
    Platform(
        id="moonshot-cn",
        name="Moonshot AI Open Platform (moonshot.cn)",
        base_url="https://api.moonshot.cn/v1",
        allowed_prefixes=["kimi-k"],
    ),
    Platform(
        id="moonshot-ai",
        name="Moonshot AI Open Platform (moonshot.ai)",
        base_url="https://api.moonshot.ai/v1",
        allowed_prefixes=["kimi-k"],
    ),
]

# 平台索引映射
_PLATFORM_BY_ID = {platform.id: platform for platform in PLATFORMS}
_PLATFORM_BY_NAME = {platform.name: platform for platform in PLATFORMS}


def get_platform_by_id(platform_id: str) -> Platform | None:
    """通过 ID 获取平台。

    Args:
        platform_id: 平台标识符。

    Returns:
        Platform 对象，未找到返回 None。
    """
    return _PLATFORM_BY_ID.get(platform_id)


def get_platform_by_name(name: str) -> Platform | None:
    """通过名称获取平台。

    Args:
        name: 平台名称。

    Returns:
        Platform 对象，未找到返回 None。
    """
    return _PLATFORM_BY_NAME.get(name)


# 托管 provider 键前缀
MANAGED_PROVIDER_PREFIX = "managed:"


def managed_provider_key(platform_id: str) -> str:
    """生成托管 provider 键。

    Args:
        platform_id: 平台标识符。

    Returns:
        托管 provider 键字符串。
    """
    return f"{MANAGED_PROVIDER_PREFIX}{platform_id}"


def managed_model_key(platform_id: str, model_id: str) -> str:
    """生成托管模型键。

    Args:
        platform_id: 平台标识符。
        model_id: 模型标识符。

    Returns:
        托管模型键字符串。
    """
    return f"{platform_id}/{model_id}"


def parse_managed_provider_key(provider_key: str) -> str | None:
    """解析托管 provider 键，提取平台 ID。

    Args:
        provider_key: Provider 键字符串。

    Returns:
        平台标识符，非托管键返回 None。
    """
    if not provider_key.startswith(MANAGED_PROVIDER_PREFIX):
        return None
    return provider_key.removeprefix(MANAGED_PROVIDER_PREFIX)


def is_managed_provider_key(provider_key: str) -> bool:
    """检查是否为托管 provider 键。

    Args:
        provider_key: Provider 键字符串。

    Returns:
        如果是托管键返回 True。
    """
    return provider_key.startswith(MANAGED_PROVIDER_PREFIX)


def get_platform_name_for_provider(provider_key: str) -> str | None:
    """获取 provider 对应的平台名称。

    Args:
        provider_key: Provider 键字符串。

    Returns:
        平台名称，非托管 provider 返回 None。
    """
    platform_id = parse_managed_provider_key(provider_key)
    if not platform_id:
        return None
    platform = get_platform_by_id(platform_id)
    return platform.name if platform else None


async def refresh_managed_models(config: Config) -> bool:
    """刷新托管平台的模型列表。

    仅在配置来自默认位置时执行刷新。

    Args:
        config: 配置对象。

    Returns:
        如果配置有变更返回 True。
    """
    if not config.is_from_default_location:
        return False

    managed_providers = {
        key: provider for key, provider in config.providers.items() if is_managed_provider_key(key)
    }
    if not managed_providers:
        return False

    changed = False
    updates: list[tuple[str, str, list[ModelInfo]]] = []
    for provider_key, provider in managed_providers.items():
        platform_id = parse_managed_provider_key(provider_key)
        if not platform_id:
            continue
        platform = get_platform_by_id(platform_id)
        if platform is None:
            logger.warning("Managed platform not found: {platform}", platform=platform_id)
            continue

        api_key = provider.api_key.get_secret_value()
        if not api_key:
            logger.warning(
                "Missing API key for managed provider: {provider}",
                provider=provider_key,
            )
            continue
        try:
            models = await list_models(platform, api_key)
        except Exception as exc:
            logger.error(
                "Failed to refresh models for {platform}: {error}",
                platform=platform_id,
                error=exc,
            )
            continue

        updates.append((provider_key, platform_id, models))
        if _apply_models(config, provider_key, platform_id, models):
            changed = True

    # 如果有变更，重新加载并保存配置
    if changed:
        config_for_save = load_config()
        save_changed = False
        for provider_key, platform_id, models in updates:
            if _apply_models(config_for_save, provider_key, platform_id, models):
                save_changed = True
        if save_changed:
            save_config(config_for_save)
    return changed


async def list_models(platform: Platform, api_key: str) -> list[ModelInfo]:
    """获取平台可用模型列表。

    Args:
        platform: 平台对象。
        api_key: API 密钥。

    Returns:
        ModelInfo 列表，已过滤允许的前缀。
    """
    async with new_client_session() as session:
        models = await _list_models(
            session,
            base_url=platform.base_url,
            api_key=api_key,
        )
    if platform.allowed_prefixes is None:
        return models
    prefixes = tuple(platform.allowed_prefixes)
    return [model for model in models if model.id.startswith(prefixes)]


async def _list_models(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    api_key: str,
) -> list[ModelInfo]:
    """内部函数：从 API 获取模型列表。

    Args:
        session: aiohttp 会话。
        base_url: API 基础 URL。
        api_key: API 密钥。

    Returns:
        ModelInfo 列表。

    Raises:
        aiohttp.ClientError: HTTP 请求失败。
        ValueError: 响应格式无效。
    """
    models_url = f"{base_url.rstrip('/')}/models"
    try:
        async with session.get(
            models_url,
            headers={"Authorization": f"Bearer {api_key}"},
            raise_for_status=True,
        ) as response:
            resp_json = await response.json()
    except aiohttp.ClientError:
        raise

    data = resp_json.get("data")
    if not isinstance(data, list):
        raise ValueError(f"Unexpected models response for {base_url}")

    result: list[ModelInfo] = []
    for item in cast(list[dict[str, Any]], data):
        model_id = item.get("id")
        if not model_id:
            continue
        result.append(
            ModelInfo(
                id=str(model_id),
                context_length=int(item.get("context_length") or 0),
                supports_reasoning=bool(item.get("supports_reasoning")),
                supports_image_in=bool(item.get("supports_image_in")),
                supports_video_in=bool(item.get("supports_video_in")),
            )
        )
    return result


def _apply_models(
    config: Config,
    provider_key: str,
    platform_id: str,
    models: list[ModelInfo],
) -> bool:
    """将模型列表应用到配置中。

    Args:
        config: 配置对象。
        provider_key: Provider 键。
        platform_id: 平台标识符。
        models: 模型信息列表。

    Returns:
        如果配置有变更返回 True。
    """
    changed = False
    model_keys: list[str] = []

    for model in models:
        model_key = managed_model_key(platform_id, model.id)
        model_keys.append(model_key)

        existing = config.models.get(model_key)
        capabilities = model.capabilities or None  # 空集合转为 None

        if existing is None:
            config.models[model_key] = LLMModel(
                provider=provider_key,
                model=model.id,
                max_context_size=model.context_length,
                capabilities=capabilities,
            )
            changed = True
            continue

        # 更新现有模型属性
        if existing.provider != provider_key:
            existing.provider = provider_key
            changed = True
        if existing.model != model.id:
            existing.model = model.id
            changed = True
        if existing.max_context_size != model.context_length:
            existing.max_context_size = model.context_length
            changed = True
        if existing.capabilities != capabilities:
            existing.capabilities = capabilities
            changed = True

    # 删除不再存在的模型
    removed_default = False
    model_keys_set = set(model_keys)
    for key, model in list(config.models.items()):
        if model.provider != provider_key:
            continue
        if key in model_keys_set:
            continue
        del config.models[key]
        if config.default_model == key:
            removed_default = True
        changed = True

    # 如果删除了默认模型，更新默认模型设置
    if removed_default:
        if model_keys:
            config.default_model = model_keys[0]
        else:
            config.default_model = next(iter(config.models), "")
        changed = True

    # 确保默认模型存在
    if config.default_model and config.default_model not in config.models:
        config.default_model = next(iter(config.models), "")
        changed = True

    return changed
