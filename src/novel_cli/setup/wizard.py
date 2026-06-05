"""首次引导向导模块。

提供精简版首次配置向导，复用其他模块的逻辑。
"""
from __future__ import annotations

from pydantic import SecretStr

from novel_cli.config import LLMModel, LLMProvider, load_config, save_config
from novel_cli.setup.components import (
    mask_api_key,
    prompt_choice,
    prompt_text,
)
from novel_cli.setup.model import prompt_capabilities
from novel_cli.setup.provider import _add_builtin_provider, _add_custom_provider
from novel_cli.setup_providers import GROUP_LABELS
from novel_cli.ui.shell.console import console


async def run_wizard() -> bool:
    """运行首次引导向导。

    首次运行时纯新增模式；已有配置时追加模式，不覆盖已有配置。

    Returns:
        bool: 是否配置成功。
    """
    console.print()
    console.print("[bold cyan]═══════════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]       Novel CLI 配置向导[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════════[/bold cyan]")
    console.print()

    cfg = load_config()
    has_existing = bool(cfg.providers) or bool(cfg.models)

    if has_existing:
        console.print(f"  已有 [bold]{len(cfg.providers)}[/bold] 个 Provider，[bold]{len(cfg.models)}[/bold] 个 Model")
        console.print("  本次配置将以追加模式进行，已有配置不会被修改")
        console.print()

    # Step 1: 选择 Provider
    provider_result = await _wizard_select_provider(cfg)
    if provider_result is None:
        return False

    provider_key, provider_type, base_url, api_key = provider_result

    # Step 2: 添加 Model
    models = await _wizard_add_models(provider_key)
    if models is None:
        return False

    # Step 3: 行为偏好
    prefs = await _wizard_preferences(cfg)
    if prefs is None:
        return False

    thinking, yolo, theme = prefs

    # 展示摘要
    _wizard_summary(
        provider_key, provider_type, base_url, api_key, models, thinking, yolo, theme
    )

    # 确认保存
    ok = await prompt_choice(
        header="请确认:",
        options=[
            ("save", "确认保存"),
            ("cancel", "取消"),
        ],
        default="save",
    )

    if ok is None or ok != "save":
        console.print("[yellow]已取消[/yellow]")
        return False

    # 写入配置
    cfg = load_config()
    cfg.providers[provider_key] = LLMProvider(
        type=provider_type,
        base_url=base_url,
        api_key=api_key,
    )

    for model_name, max_ctx, caps in models:
        full_key = f"{provider_key}/{model_name}"
        cfg.models[full_key] = LLMModel(
            provider=provider_key,
            model=model_name,
            max_context_size=max_ctx,
            capabilities=caps if caps else None,
        )

    # 只有在没有 default_model 时才设置
    if not cfg.default_model and models:
        cfg.default_model = f"{provider_key}/{models[0][0]}"

    cfg.default_thinking = thinking
    cfg.default_yolo = yolo
    cfg.theme = theme

    save_config(cfg)
    console.print("[green]配置已保存![/green]")
    console.print(f"  配置文件: [dim]~/.novel/config.toml[/dim]")
    console.print()
    return True


async def _wizard_select_provider(
    cfg: object,
) -> tuple[str, str, str, SecretStr] | None:
    """向导 Step 1: 选择 Provider。

    如果已有 provider，允许选择复用或新增。

    Args:
        cfg: 配置对象。

    Returns:
        tuple: (key, type, base_url, api_key) 或 None。
    """
    console.print("[bold]Step 1/3: 选择 Provider[/bold]")
    console.print()

    providers = getattr(cfg, "providers", {})
    if providers:
        options = [(k, f"{k} (已配置)") for k in providers]
        options.append(("__new__", "新增 Provider..."))

        choice = await prompt_choice(
            header="选择 Provider:",
            options=options,
        )
        if choice is None:
            return None

        if choice != "__new__":
            p = providers[choice]
            return (choice, p.type, p.base_url, p.api_key)

    # 新增 provider
    group_options = [
        ("domestic", f"{GROUP_LABELS['domestic']}  (智谱 AI, Kimi, Moonshot AI)"),
        ("international", f"{GROUP_LABELS['international']}  (Anthropic, OpenAI, Google GenAI, OpenRouter)"),
        ("custom", f"{GROUP_LABELS['custom']}  (DeepSeek, Qwen, Groq, ...)"),
    ]

    group_choice = await prompt_choice(
        header="选择平台分组:",
        options=group_options,
    )
    if group_choice is None:
        return None

    if group_choice == "custom":
        return await _add_custom_provider()

    return await _add_builtin_provider(group_choice)


async def _wizard_add_models(
    provider_key: str,
) -> list[tuple[str, int, set]] | None:
    """向导 Step 2: 添加模型。

    Args:
        provider_key: Provider key。

    Returns:
        list[tuple[name, max_context_size, capabilities]] 或 None。
    """
    console.print("[bold]Step 2/3: 添加模型[/bold]")
    console.print()

    from novel_cli.setup_providers import get_provider_def_by_id

    provider_def = get_provider_def_by_id(provider_key)
    recommended = provider_def.recommended_max_context_size if provider_def else 200000

    models: list[tuple[str, int, set]] = []

    while True:
        model_name = await prompt_text(
            "Model 名称 (如 glm-5.1, claude-sonnet-4-5, deepseek-chat)"
        )
        if not model_name:
            if not models:
                return None
            break

        size_str = await prompt_text(
            f"max_context_size (留空使用推荐值: {recommended})"
        )
        if size_str:
            try:
                max_context_size = int(size_str)
            except ValueError:
                max_context_size = recommended
        else:
            max_context_size = recommended

        capabilities = await prompt_capabilities()
        if capabilities is None:
            return None

        models.append((model_name, max_context_size, capabilities))
        console.print(f"[green]已添加: {provider_key}/{model_name}[/green]")
        console.print()

        next_action = await prompt_choice(
            header="接下来？",
            options=[
                ("add", "继续添加模型"),
                ("done", "完成，进入下一步"),
            ],
            default="done",
        )
        if next_action is None or next_action == "done":
            break

    if not models:
        return None

    return models


async def _wizard_preferences(
    cfg: object,
) -> tuple[bool, bool, str] | None:
    """向导 Step 3: 行为偏好。

    Args:
        cfg: 配置对象，用于获取默认值。

    Returns:
        tuple: (thinking, yolo, theme) 或 None。
    """
    console.print("[bold]Step 3/3: 行为偏好[/bold]")
    console.print()

    current_theme = getattr(cfg, "theme", "dark")

    thinking_choice = await prompt_choice(
        header="Thinking 模式 (模型会深度思考后再回答):",
        options=[
            ("on", "启用（模型会深度思考后再回答）"),
            ("off", "禁用（直接回答，速度更快）"),
        ],
    )
    if thinking_choice is None:
        return None

    yolo_choice = await prompt_choice(
        header="YOLO 模式（自动批准工具调用）:",
        options=[
            ("off", "禁用（每次工具调用需要确认）"),
            ("on", "启用（自动执行，无需确认）"),
        ],
    )
    if yolo_choice is None:
        return None

    theme_choice = await prompt_choice(
        header="终端主题:",
        options=[
            ("dark", "dark"),
            ("light", "light"),
        ],
        default=current_theme,
    )
    if theme_choice is None:
        return None

    return thinking_choice == "on", yolo_choice == "on", theme_choice


def _wizard_summary(
    provider_key: str,
    provider_type: str,
    base_url: str,
    api_key: SecretStr,
    models: list[tuple[str, int, set]],
    thinking: bool,
    yolo: bool,
    theme: str,
) -> None:
    """展示配置摘要。

    Args:
        provider_key: Provider key。
        provider_type: Provider 类型。
        base_url: API Base URL。
        api_key: API 密钥。
        models: 模型列表。
        thinking: Thinking 模式。
        yolo: YOLO 模式。
        theme: 终端主题。
    """
    console.print("[bold cyan]═══════════ 配置摘要 ═══════════[/bold cyan]")
    console.print()
    console.print("[Provider]")
    console.print(f"  名称:     {provider_key}")
    console.print(f"  类型:     {provider_type}")
    console.print(f"  Base URL: {base_url or '(未设置)'}")
    console.print(f"  API Key:  {mask_api_key(api_key)}")
    console.print()
    console.print("[Models]")
    for name, ctx, caps in models:
        caps_str = f", caps: {sorted(caps)}" if caps else ""
        console.print(f"  {provider_key}/{name}  (context: {ctx}{caps_str})")
    console.print()
    console.print("[偏好]")
    console.print(f"  Thinking: {'启用' if thinking else '禁用'}")
    console.print(f"  YOLO:    {'启用' if yolo else '禁用'}")
    console.print(f"  主题:     {theme}")
    console.print()
    console.print("[bold cyan]═══════════════════════════════[/bold cyan]")
    console.print()
