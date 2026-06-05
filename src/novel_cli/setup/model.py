"""Model 管理模块。

提供 Model 的查看、添加、编辑、删除功能。
"""
from __future__ import annotations

from typing import cast

from novel_cli.config import LLMModel, load_config, save_config
from novel_cli.llm import ModelCapability
from novel_cli.setup.components import (
    prompt_choice,
    prompt_confirm,
    prompt_text,
)
from novel_cli.setup_providers import get_provider_def_by_id
from novel_cli.ui.shell.console import console


async def run_model_menu() -> bool:
    """Model 管理子菜单。

    Returns:
        bool: 操作是否成功。
    """
    console.print()
    console.print("[bold]── Model 管理 ──[/bold]")
    console.print()

    while True:
        choice = await prompt_choice(
            header="Model 管理 (↑↓ 导航, Enter 选择, Ctrl+C 返回):",
            options=[
                ("view", "查看已有 Model"),
                ("add", "添加 Model"),
                ("edit", "编辑 Model"),
                ("delete", "删除 Model"),
                ("back", "返回"),
            ],
            default="back",
        )

        if choice is None or choice == "back":
            return True

        if choice == "view":
            _view_models()
        elif choice == "add":
            ok = await _add_model()
            if ok:
                console.print("[green]Model 已添加![/green]")
                console.print()
        elif choice == "edit":
            ok = await _edit_model()
            if ok:
                console.print("[green]Model 已更新![/green]")
                console.print()
        elif choice == "delete":
            ok = await _delete_model()
            if ok:
                console.print("[green]Model 已删除![/green]")
                console.print()


def _view_models() -> None:
    """查看已有 Model，按 provider 分组展示。"""
    cfg = load_config()

    if not cfg.models:
        console.print("[yellow]暂无已配置的 Model[/yellow]")
        console.print()
        return

    # 按 provider 分组
    by_provider: dict[str, list[tuple[str, LLMModel]]] = {}
    for key, model in cfg.models.items():
        by_provider.setdefault(model.provider, []).append((key, model))

    console.print("[bold]已配置的 Model：[/bold]")
    console.print()

    for provider_key, models in by_provider.items():
        console.print(f"  ── [bold]{provider_key}[/bold] ──")
        for key, model in models:
            caps_str = ", ".join(sorted(model.capabilities)) if model.capabilities else ""
            cap_display = f"   caps: {caps_str}" if caps_str else ""
            default_mark = " [dim]* ← default_model[/dim]" if key == cfg.default_model else ""
            console.print(f"    {model.model}   ctx: {model.max_context_size}{cap_display}{default_mark}")
        console.print()


async def _add_model() -> bool:
    """添加新 Model。

    Returns:
        bool: 是否添加成功。
    """
    cfg = load_config()

    if not cfg.providers:
        console.print("[yellow]请先添加 Provider[/yellow]")
        return False

    # 选择目标 provider
    provider_options = [(k, f"{k} ({p.type})") for k, p in cfg.providers.items()]
    provider_key = await prompt_choice(
        header="选择目标 Provider:",
        options=provider_options,
    )
    if provider_key is None:
        return False

    # 输入 model name
    model_name = await prompt_text(
        "Model 名称 (如 glm-5.1, claude-sonnet-4-5, deepseek-chat)"
    )
    if not model_name:
        return False

    # 检查重复
    full_key = f"{provider_key}/{model_name}"
    if full_key in cfg.models:
        console.print(f"[yellow]模型 '{full_key}' 已存在[/yellow]")
        return False

    # max_context_size
    provider_def = get_provider_def_by_id(provider_key)
    recommended = provider_def.recommended_max_context_size if provider_def else 200000

    size_str = await prompt_text(
        f"max_context_size (留空使用推荐值: {recommended})"
    )
    if size_str:
        try:
            max_context_size = int(size_str)
        except ValueError:
            console.print(f"[yellow]无效的数值，使用推荐值: {recommended}[/yellow]")
            max_context_size = recommended
    else:
        max_context_size = recommended

    # capabilities
    capabilities = await prompt_capabilities()
    if capabilities is None:
        return False

    # 保存
    cfg.models[full_key] = LLMModel(
        provider=provider_key,
        model=model_name,
        max_context_size=max_context_size,
        capabilities=capabilities if capabilities else None,
    )

    # 如果是第一个 model 或没有 default_model，自动设为 default
    if not cfg.default_model:
        cfg.default_model = full_key

    save_config(cfg)
    return True


async def _edit_model() -> bool:
    """编辑 Model。

    Returns:
        bool: 是否编辑成功。
    """
    cfg = load_config()

    if not cfg.models:
        console.print("[yellow]暂无已配置的 Model[/yellow]")
        return False

    # 选择要编辑的 model
    options = [
        (key, f"{key} (ctx: {m.max_context_size})")
        for key, m in cfg.models.items()
    ]
    choice = await prompt_choice(
        header="选择要编辑的 Model:",
        options=options,
    )
    if choice is None:
        return False

    model = cfg.models[choice]

    # 选择要编辑的字段
    caps_display = ", ".join(sorted(model.capabilities)) if model.capabilities else "无"
    field_choice = await prompt_choice(
        header=f"编辑 {choice}:",
        options=[
            ("max_context_size", f"max_context_size (当前: {model.max_context_size})"),
            ("capabilities", f"capabilities (当前: {caps_display})"),
            ("cancel", "取消"),
        ],
        default="cancel",
    )
    if field_choice is None or field_choice == "cancel":
        return False

    if field_choice == "max_context_size":
        size_str = await prompt_text(
            f"新的 max_context_size (当前: {model.max_context_size})"
        )
        if size_str is None:
            return False
        if size_str:
            try:
                new_size = int(size_str)
            except ValueError:
                console.print("[yellow]无效的数值[/yellow]")
                return False
        else:
            new_size = model.max_context_size

        cfg.models[choice] = LLMModel(
            provider=model.provider,
            model=model.model,
            max_context_size=new_size,
            capabilities=model.capabilities,
        )

    elif field_choice == "capabilities":
        new_caps = await prompt_capabilities(
            default_capabilities=model.capabilities or set()
        )
        if new_caps is None:
            return False

        cfg.models[choice] = LLMModel(
            provider=model.provider,
            model=model.model,
            max_context_size=model.max_context_size,
            capabilities=new_caps if new_caps else None,
        )

    save_config(cfg)
    return True


async def _delete_model() -> bool:
    """删除 Model，检查 default_model 引用。

    Returns:
        bool: 是否删除成功。
    """
    cfg = load_config()

    if not cfg.models:
        console.print("[yellow]暂无已配置的 Model[/yellow]")
        return False

    # 选择要删除的 model
    options = [(key, key) for key in cfg.models]
    choice = await prompt_choice(
        header="选择要删除的 Model:",
        options=options,
    )
    if choice is None:
        return False

    # 检查 default_model
    if choice == cfg.default_model:
        remaining = [k for k in cfg.models if k != choice]
        if not remaining:
            console.print("[red]至少需要保留一个 Model[/red]")
            return False

        console.print(f"[yellow]'{choice}' 是当前默认模型，请选择新的默认模型:[/yellow]")
        model_options = [(k, k) for k in remaining]
        new_default = await prompt_choice(
            header="选择新的默认模型:",
            options=model_options,
        )
        if new_default is None:
            return False

        cfg.default_model = new_default

    ok = await prompt_confirm(f"确认删除 Model '{choice}'？")
    if not ok:
        return False

    del cfg.models[choice]
    save_config(cfg)
    return True


async def prompt_capabilities(
    default_capabilities: set[ModelCapability] | None = None,
) -> set[ModelCapability] | None:
    """循环选择模型能力，支持互斥约束。

    thinking 和 always_thinking 互斥。

    Args:
        default_capabilities: 默认选中的能力集合。

    Returns:
        set[ModelCapability]: 用户选择的能力集合。
        None: 用户取消。
    """
    if default_capabilities is None:
        default_capabilities = {"thinking"}

    selected: set[ModelCapability] = set(default_capabilities)

    # 处理互斥
    if "thinking" in selected and "always_thinking" in selected:
        selected.discard("thinking")

    while True:
        options: list[tuple[str, str]] = []
        for cap in ("thinking", "image_in", "video_in", "always_thinking"):
            status = "[x]" if cap in selected else "[ ]"
            options.append((cap, f"{status} {cap}"))

        options.append(("done", "完成选择"))
        options.append(("cancel", "取消"))

        console.print(f"  当前已选能力: {sorted(selected) if selected else '无'}")

        choice = await prompt_choice(
            header="选择模型能力:",
            options=options,
        )

        if choice is None or choice == "cancel":
            return None

        if choice == "done":
            return selected

        cap_choice = cast(ModelCapability, choice)
        if cap_choice == "always_thinking":
            selected.discard("thinking")
            selected.add("always_thinking")
        elif cap_choice == "thinking":
            selected.discard("always_thinking")
            selected.add("thinking")
        else:
            if cap_choice in selected:
                selected.discard(cap_choice)
            else:
                selected.add(cap_choice)
