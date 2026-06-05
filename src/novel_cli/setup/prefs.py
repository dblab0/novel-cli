"""偏好设置模块。

提供全局偏好配置的查看和编辑功能。
"""
from __future__ import annotations

from novel_cli.config import Config, load_config, save_config
from novel_cli.setup.components import prompt_choice, prompt_text
from novel_cli.ui.shell.console import console


async def run_prefs_menu() -> bool:
    """偏好设置编辑菜单。

    Returns:
        bool: 操作是否成功。
    """
    console.print()
    console.print("[bold]── 偏好设置 ──[/bold]")
    console.print()

    while True:
        cfg = load_config()

        console.print("  1. default_model       → " + (cfg.default_model or "(未设置)"))
        console.print(f"  2. default_thinking    → {cfg.default_thinking}")
        console.print(f"  3. default_yolo        → {cfg.default_yolo}")
        console.print(f"  4. default_plan_mode   → {cfg.default_plan_mode}")
        console.print(f"  5. default_editor      → " + (cfg.default_editor or "(未设置)"))
        console.print(f"  6. theme               → {cfg.theme}")
        console.print()

        choice = await prompt_text("输入编号编辑，0 返回")
        if choice is None or choice == "0":
            return True

        modified = False

        if choice == "1":
            modified = await _edit_default_model(cfg)
        elif choice == "2":
            modified = await _edit_bool(cfg, "default_thinking", "Thinking 模式")
        elif choice == "3":
            modified = await _edit_bool(cfg, "default_yolo", "YOLO 模式")
        elif choice == "4":
            modified = await _edit_bool(cfg, "default_plan_mode", "Plan Mode")
        elif choice == "5":
            modified = await _edit_editor(cfg)
        elif choice == "6":
            modified = await _edit_theme(cfg)
        else:
            console.print("[yellow]无效的编号[/yellow]")

        if modified:
            console.print("[green]偏好已更新![/green]")
            console.print()


async def _edit_default_model(cfg: Config) -> bool:
    """编辑默认模型。

    Args:
        cfg: 配置对象。

    Returns:
        bool: 是否修改成功。
    """
    if not cfg.models:
        console.print("[yellow]暂无可用的 Model[/yellow]")
        return False

    options = [(k, k) for k in cfg.models]
    choice = await prompt_choice(
        header="选择默认模型:",
        options=options,
        default=cfg.default_model if cfg.default_model in cfg.models else None,
    )
    if choice is None:
        return False

    cfg.default_model = choice
    save_config(cfg)
    return True


async def _edit_bool(cfg: Config, field: str, label: str) -> bool:
    """编辑布尔类型偏好。

    Args:
        cfg: 配置对象。
        field: 字段名。
        label: 显示标签。

    Returns:
        bool: 是否修改成功。
    """
    current = getattr(cfg, field)
    choice = await prompt_choice(
        header=f"{label} (当前: {'启用' if current else '禁用'}):",
        options=[
            ("on", "启用"),
            ("off", "禁用"),
        ],
        default="on" if current else "off",
    )
    if choice is None:
        return False

    setattr(cfg, field, choice == "on")
    save_config(cfg)
    return True


async def _edit_editor(cfg: Config) -> bool:
    """编辑默认编辑器。

    Args:
        cfg: 配置对象。

    Returns:
        bool: 是否修改成功。
    """
    new_editor = await prompt_text(
        f"默认编辑器命令 (当前: {cfg.default_editor or '未设置'}，留空跟随系统)"
    )
    if new_editor is None:
        return False

    cfg.default_editor = new_editor
    save_config(cfg)
    return True


async def _edit_theme(cfg: Config) -> bool:
    """编辑主题。

    Args:
        cfg: 配置对象。

    Returns:
        bool: 是否修改成功。
    """
    choice = await prompt_choice(
        header=f"终端主题 (当前: {cfg.theme}):",
        options=[
            ("dark", "dark"),
            ("light", "light"),
        ],
        default=cfg.theme,
    )
    if choice is None:
        return False

    cfg.theme = choice  # type: ignore[assignment]
    save_config(cfg)
    return True
