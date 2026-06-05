"""Setup 向导使用的 Provider 定义数据。

包含分组信息、默认 base_url、推荐 max_context_size 等元数据，
供 setup 交互流程使用。
"""
from __future__ import annotations

from typing import Literal, NamedTuple

from novel_cli.llm import ProviderType

# 分组标签
ProviderGroup = Literal["domestic", "international", "custom"]

GROUP_LABELS: dict[ProviderGroup, str] = {
    "domestic": "国内平台",
    "international": "国际平台",
    "custom": "自定义 Provider",
}


class ProviderDef(NamedTuple):
    """内置 Provider 的定义信息。"""

    id: str
    """唯一标识，用于 config 中 providers 的 key。"""
    display_name: str
    """在选择界面中显示的名称。"""
    group: ProviderGroup
    """所属分组（国内/国际/自定义）。"""
    provider_type: ProviderType
    """对应的 ProviderType，决定使用哪个 kosong provider。"""
    base_url: str
    """默认的 API base URL。"""
    recommended_max_context_size: int
    """推荐的 max_context_size。"""


# ── 国内平台 ──

ZHIPU = ProviderDef(
    id="zhipu",
    display_name="智谱 AI",
    group="domestic",
    provider_type="zhipu",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    recommended_max_context_size=128000,
)

KIMI_CN = ProviderDef(
    id="kimi-cn",
    display_name="Kimi (Moonshot CN)",
    group="domestic",
    provider_type="kimi",
    base_url="https://api.moonshot.cn/v1",
    recommended_max_context_size=131072,
)

MOONSHOT_AI = ProviderDef(
    id="moonshot-ai",
    display_name="Moonshot AI",
    group="domestic",
    provider_type="kimi",
    base_url="https://api.moonshot.ai/v1",
    recommended_max_context_size=131072,
)

# ── 国际平台 ──

ANTHROPIC = ProviderDef(
    id="anthropic",
    display_name="Anthropic",
    group="international",
    provider_type="anthropic",
    base_url="https://api.anthropic.com",
    recommended_max_context_size=200000,
)

GOOGLE_GENAI = ProviderDef(
    id="google-genai",
    display_name="Google GenAI",
    group="international",
    provider_type="google_genai",
    base_url="",
    recommended_max_context_size=1000000,
)

OPENAI = ProviderDef(
    id="openai",
    display_name="OpenAI",
    group="international",
    provider_type="openai_legacy",
    base_url="https://api.openai.com/v1",
    recommended_max_context_size=128000,
)

OPENROUTER = ProviderDef(
    id="openrouter",
    display_name="OpenRouter",
    group="international",
    provider_type="openrouter",
    base_url="https://openrouter.ai/api/v1",
    recommended_max_context_size=128000,
)


# 按 group 分组
BUILTIN_PROVIDERS: list[ProviderDef] = [
    # 国内
    ZHIPU,
    KIMI_CN,
    MOONSHOT_AI,
    # 国际
    ANTHROPIC,
    GOOGLE_GENAI,
    OPENAI,
    OPENROUTER,
]


def get_providers_by_group(group: ProviderGroup) -> list[ProviderDef]:
    """获取指定分组的 provider 列表。"""
    return [p for p in BUILTIN_PROVIDERS if p.group == group]


def get_provider_def_by_id(provider_id: str) -> ProviderDef | None:
    """根据 ID 查找 provider 定义。"""
    for p in BUILTIN_PROVIDERS:
        if p.id == provider_id:
            return p
    return None


# 自定义 provider 可选的 API 接口类型
CUSTOM_API_TYPES: list[tuple[ProviderType, str]] = [
    ("openai_legacy", "OpenAI 兼容"),
    ("anthropic", "Anthropic 兼容"),
    ("google_genai", "Google GenAI 兼容"),
    ("openai_responses", "OpenAI Responses API"),
]
