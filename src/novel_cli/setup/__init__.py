"""Setup 模块入口。

提供混合模式：交互主菜单 + 子命令直达。
"""
from __future__ import annotations

from novel_cli.ui.shell.console import console


async def run_setup(section: str | None = None) -> bool:
    """setup 入口。section=None 时进主菜单，否则直接跳对应模块。

    Args:
        section: 配置模块名称，可选 provider|model|prefs|services|wizard。

    Returns:
        bool: 操作是否成功。
    """
    match section:
        case "provider":
            from novel_cli.setup.provider import run_provider_menu
            return await run_provider_menu()
        case "model":
            from novel_cli.setup.model import run_model_menu
            return await run_model_menu()
        case "prefs":
            from novel_cli.setup.prefs import run_prefs_menu
            return await run_prefs_menu()
        case "services":
            from novel_cli.setup.services import run_services_menu
            return await run_services_menu()
        case "wizard":
            from novel_cli.setup.wizard import run_wizard
            return await run_wizard()
        case None:
            return await show_main_menu()
        case _:
            console.print(f"[red]未知配置模块: {section}[/red]")
            console.print("可用模块: provider, model, prefs, services, wizard")
            return False


async def show_main_menu() -> bool:
    """展示交互主菜单。

    Returns:
        bool: 操作是否成功。
    """
    console.print()
    console.print("[bold cyan]═══════════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]       Novel CLI 配置管理[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════════[/bold cyan]")
    console.print()

    from novel_cli.setup.components import prompt_choice

    while True:
        choice = await prompt_choice(
            header="请选择操作 (↑↓ 导航, Enter 选择, Ctrl+C 退出):",
            options=[
                ("provider", "管理 Provider"),
                ("model", "管理 Model"),
                ("prefs", "编辑偏好设置"),
                ("services", "配置服务 (novel_db)"),
                ("wizard", "首次引导向导"),
                ("exit", "退出"),
            ],
            default="exit",
        )

        if choice is None or choice == "exit":
            return True

        await run_setup(choice)
