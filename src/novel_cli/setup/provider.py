"""Provider 管理模块。

提供 Provider 的查看、添加、编辑、删除功能。
"""
from __future__ import annotations

from pydantic import SecretStr

from novel_cli.config import LLMProvider, load_config, save_config
from novel_cli.setup.components import (
    mask_api_key,
    prompt_choice,
    prompt_confirm,
    prompt_text,
)
from novel_cli.setup_providers import (
    CUSTOM_API_TYPES,
    GROUP_LABELS,
    ProviderDef,
    get_provider_def_by_id,
    get_providers_by_group,
)
from novel_cli.ui.shell.console import console


async def run_provider_menu() -> bool:
    """Provider 管理子菜单。

    Returns:
        bool: 操作是否成功。
    """
    console.print()
    console.print("[bold]── Provider 管理 ──[/bold]")
    console.print()

    while True:
        choice = await prompt_choice(
            header="Provider 管理 (↑↓ 导航, Enter 选择, Ctrl+C 返回):",
            options=[
                ("view", "查看已有 Provider"),
                ("add", "添加 Provider"),
                ("edit", "编辑 Provider"),
                ("delete", "删除 Provider"),
                ("back", "返回"),
            ],
            default="back",
        )

        if choice is None or choice == "back":
            return True

        if choice == "view":
            _view_providers()
        elif choice == "add":
            ok = await _add_provider()
            if ok:
                console.print("[green]Provider 已添加![/green]")
                console.print()
        elif choice == "edit":
            ok = await _edit_provider()
            if ok:
                console.print("[green]Provider 已更新![/green]")
                console.print()
        elif choice == "delete":
            ok = await _delete_provider()
            if ok:
                console.print("[green]Provider 已删除![/green]")
                console.print()


def _view_providers() -> None:
    """查看已有 Provider。"""
    cfg = load_config()

    if not cfg.providers:
        console.print("[yellow]暂无已配置的 Provider[/yellow]")
        console.print()
        return

    console.print("[bold]已配置的 Provider：[/bold]")
    console.print()

    for i, (key, provider) in enumerate(cfg.providers.items(), 1):
        provider_def = get_provider_def_by_id(key)
        display_name = f" ({provider_def.display_name})" if provider_def else ""
        console.print(f"  {i}. [bold]{key}{display_name}[/bold]")
        console.print(f"     type: {provider.type} | base_url: {provider.base_url or '(未设置)'}")
        console.print(f"     api_key: {mask_api_key(provider.api_key)}")
        console.print()


async def _add_provider() -> bool:
    """添加新 Provider。

    Returns:
        bool: 是否添加成功。
    """
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
        return False

    if group_choice == "custom":
        result = await _add_custom_provider()
    else:
        result = await _add_builtin_provider(group_choice)

    if result is None:
        return False

    provider_key, provider_type, base_url, api_key = result

    cfg = load_config()
    cfg.providers[provider_key] = LLMProvider(
        type=provider_type,
        base_url=base_url,
        api_key=api_key,
    )
    save_config(cfg)
    return True


async def _add_builtin_provider(group: str) -> tuple[str, str, str, SecretStr] | None:
    """添加内置 Provider。

    Args:
        group: 平台分组。

    Returns:
        tuple: (key, type, base_url, api_key) 或 None 表示取消。
    """
    providers = get_providers_by_group(group)  # type: ignore[arg-type]
    provider_options = [(p.id, p.display_name) for p in providers]

    provider_id = await prompt_choice(
        header="选择 Provider:",
        options=provider_options,
    )
    if provider_id is None:
        return None

    provider_def = next((p for p in providers if p.id == provider_id), None)
    if provider_def is None:
        console.print("[red]未知的 Provider[/red]")
        return None

    api_key_str, base_url = await _prompt_connection_params(
        provider_def.base_url
    )
    if api_key_str is None:
        return None

    return (provider_def.id, provider_def.provider_type, base_url, SecretStr(api_key_str))


async def _add_custom_provider() -> tuple[str, str, str, SecretStr] | None:
    """添加自定义 Provider。

    Returns:
        tuple: (key, type, base_url, api_key) 或 None 表示取消。
    """
    name = await prompt_text("服务商名称 (如 deepseek, qwen, groq)")
    if not name:
        return None

    name = "".join(c for c in name if c.isalnum() or c in "-_").lower()
    if not name:
        console.print("[red]名称无效[/red]")
        return None

    cfg = load_config()
    if name in cfg.providers:
        console.print(f"[yellow]Provider '{name}' 已存在，请使用编辑功能[/yellow]")
        return None

    type_options = [(str(t), label) for t, label in CUSTOM_API_TYPES]
    type_choice = await prompt_choice(
        header="选择 API 接口类型:",
        options=type_options,
    )
    if type_choice is None:
        return None

    base_url = await prompt_text("Base URL (如 https://api.deepseek.com/v1)")
    if base_url is None:
        return None

    api_key_str = await prompt_text("API Key", is_password=True)
    if not api_key_str:
        return None

    return (name, type_choice, base_url, SecretStr(api_key_str))  # type: ignore[arg-type]


async def _prompt_connection_params(
    default_base_url: str,
) -> tuple[str | None, str]:
    """填写连接参数（API Key + Base URL）。

    Args:
        default_base_url: 默认 base_url。

    Returns:
        tuple: (api_key, base_url) 或 (None, "") 表示取消。
    """
    api_key = await prompt_text("API Key", is_password=True)
    if not api_key:
        return None, ""

    if default_base_url:
        base_url = await prompt_text(
            f"Base URL (留空使用默认: {default_base_url})"
        )
        base_url = base_url if base_url else default_base_url
    else:
        base_url = await prompt_text("Base URL (留空跳过)")
        if not base_url:
            base_url = ""

    return api_key, base_url


async def _edit_provider() -> bool:
    """编辑 Provider。

    Returns:
        bool: 是否编辑成功。
    """
    cfg = load_config()

    if not cfg.providers:
        console.print("[yellow]暂无已配置的 Provider[/yellow]")
        return False

    options = [(k, f"{k} ({p.type})") for k, p in cfg.providers.items()]
    choice = await prompt_choice(
        header="选择要编辑的 Provider:",
        options=options,
    )
    if choice is None:
        return False

    provider = cfg.providers[choice]

    field_choice = await prompt_choice(
        header=f"编辑 {choice}:",
        options=[
            ("api_key", "API Key"),
            ("base_url", "Base URL"),
            ("type", "Provider Type"),
            ("cancel", "取消"),
        ],
        default="cancel",
    )
    if field_choice is None or field_choice == "cancel":
        return False

    if field_choice == "api_key":
        new_key = await prompt_text("新的 API Key", is_password=True)
        if not new_key:
            return False
        cfg.providers[choice] = LLMProvider(
            type=provider.type,
            base_url=provider.base_url,
            api_key=SecretStr(new_key),
            env=provider.env,
            custom_headers=provider.custom_headers,
        )
    elif field_choice == "base_url":
        new_url = await prompt_text(
            f"新的 Base URL (当前: {provider.base_url or '(未设置)'})"
        )
        if new_url is None:
            return False
        url = new_url if new_url else provider.base_url
        cfg.providers[choice] = LLMProvider(
            type=provider.type,
            base_url=url,
            api_key=provider.api_key,
            env=provider.env,
            custom_headers=provider.custom_headers,
        )
    elif field_choice == "type":
        type_options = [(str(t), label) for t, label in CUSTOM_API_TYPES]
        new_type = await prompt_choice(
            header=f"选择新的 Provider Type (当前: {provider.type}):",
            options=type_options,
        )
        if new_type is None:
            return False
        cfg.providers[choice] = LLMProvider(
            type=new_type,  # type: ignore[arg-type]
            base_url=provider.base_url,
            api_key=provider.api_key,
            env=provider.env,
            custom_headers=provider.custom_headers,
        )

    save_config(cfg)
    return True


async def _delete_provider() -> bool:
    """删除 Provider，联动检查关联 model 和 default_model。

    Returns:
        bool: 是否删除成功。
    """
    cfg = load_config()

    if not cfg.providers:
        console.print("[yellow]暂无已配置的 Provider[/yellow]")
        return False

    options = [(k, f"{k} ({p.type})") for k, p in cfg.providers.items()]
    choice = await prompt_choice(
        header="选择要删除的 Provider:",
        options=options,
    )
    if choice is None:
        return False

    # 检查关联 model
    related_models = [
        key for key, model in cfg.models.items()
        if model.provider == choice
    ]

    if related_models:
        console.print(f"[yellow]Provider '{choice}' 关联了以下模型:[/yellow]")
        for m in related_models:
            console.print(f"  - {m}")
        ok = await prompt_confirm("是否一并删除这些关联模型？")
        if not ok:
            return False

        for m in related_models:
            del cfg.models[m]

    # 检查 default_model 引用
    if cfg.default_model in related_models:
        remaining_models = [k for k in cfg.models if k not in related_models]
        if remaining_models:
            console.print(f"[yellow]当前默认模型 '{cfg.default_model}' 将被删除，请选择新的默认模型:[/yellow]")
            model_options = [(k, k) for k in remaining_models]
            new_default = await prompt_choice(
                header="选择新的默认模型:",
                options=model_options,
            )
            if new_default is None:
                return False
            cfg.default_model = new_default
        else:
            cfg.default_model = ""

    ok = await prompt_confirm(f"确认删除 Provider '{choice}'？")
    if not ok:
        return False

    del cfg.providers[choice]
    save_config(cfg)
    return True
