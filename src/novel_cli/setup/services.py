"""服务配置模块。

提供 novel_db 等服务配置的查看和编辑功能。
"""
from __future__ import annotations

from pydantic import SecretStr

from novel_cli.config import load_config, save_config
from novel_cli.setup.components import mask_api_key, prompt_text
from novel_cli.ui.shell.console import console


async def run_services_menu() -> bool:
    """服务配置编辑菜单（直接展示 novel_db 字段）。

    Returns:
        bool: 操作是否成功。
    """
    console.print()
    console.print("[bold]── 服务配置: novel_db ──[/bold]")
    console.print()

    while True:
        cfg = load_config()
        db = cfg.services.novel_db

        console.print("  ── PostgreSQL ──")
        console.print(f"  1. pg_host       → {db.pg_host}")
        console.print(f"  2. pg_port       → {db.pg_port}")
        console.print(f"  3. pg_db         → {db.pg_db}")
        console.print(f"  4. pg_user       → {db.pg_user}")
        console.print(f"  5. pg_password   → {mask_api_key(db.pg_password)}")
        console.print()
        console.print("  ── Embedding ──")
        console.print(f"  6. embedding_api_url   → {db.embedding_api_url or '(未设置)'}")
        console.print(f"  7. embedding_api_key   → {mask_api_key(db.embedding_api_key)}")
        console.print(f"  8. embedding_model     → {db.embedding_model}")
        console.print(f"  9. embedding_dim       → {db.embedding_dim}")
        console.print()
        console.print(f"  10. data_dir    → {db.data_dir or '(留空则使用 ./data/input_data/)'}")
        console.print(f"  11. pg_pool_size → {db.pg_pool_size}")
        console.print()

        choice = await prompt_text("输入编号编辑，0 返回")
        if choice is None or choice == "0":
            return True

        modified = False

        # PostgreSQL 字段
        if choice == "1":
            modified = await _edit_str_field(db, "pg_host", "pg_host")
        elif choice == "2":
            modified = await _edit_int_field(db, "pg_port", "pg_port")
        elif choice == "3":
            modified = await _edit_str_field(db, "pg_db", "pg_db")
        elif choice == "4":
            modified = await _edit_str_field(db, "pg_user", "pg_user")
        elif choice == "5":
            modified = await _edit_secret_field(db, "pg_password", "pg_password")

        # Embedding 字段
        elif choice == "6":
            modified = await _edit_str_field(db, "embedding_api_url", "embedding_api_url")
        elif choice == "7":
            modified = await _edit_secret_field(db, "embedding_api_key", "embedding_api_key")
        elif choice == "8":
            modified = await _edit_str_field(db, "embedding_model", "embedding_model")
        elif choice == "9":
            modified = await _edit_int_field(db, "embedding_dim", "embedding_dim")

        # 其他字段
        elif choice == "10":
            modified = await _edit_str_field(
                db, "data_dir", "data_dir", hint="留空则使用 ./data/input_data/"
            )
        elif choice == "11":
            modified = await _edit_int_field(db, "pg_pool_size", "pg_pool_size")
        else:
            console.print("[yellow]无效的编号[/yellow]")

        if modified:
            save_config(cfg)
            console.print("[green]服务配置已更新![/green]")
            console.print()


async def _edit_str_field(
    db: object,
    field: str,
    label: str,
    hint: str = "",
) -> bool:
    """编辑字符串字段。

    Args:
        db: 配置对象。
        field: 字段名。
        label: 显示标签。
        hint: 输入提示。

    Returns:
        bool: 是否修改成功。
    """
    current = getattr(db, field)
    hint_text = f" ({hint})" if hint else ""
    new_val = await prompt_text(f"新的 {label}{hint_text} (当前: {current or '(空)'})")
    if new_val is None:
        return False

    setattr(db, field, new_val)
    return True


async def _edit_int_field(
    db: object,
    field: str,
    label: str,
) -> bool:
    """编辑整数字段。

    Args:
        db: 配置对象。
        field: 字段名。
        label: 显示标签。

    Returns:
        bool: 是否修改成功。
    """
    current = getattr(db, field)
    new_val = await prompt_text(f"新的 {label} (当前: {current})")
    if new_val is None:
        return False

    if not new_val:
        return False

    try:
        setattr(db, field, int(new_val))
        return True
    except ValueError:
        console.print("[yellow]无效的数值[/yellow]")
        return False


async def _edit_secret_field(
    db: object,
    field: str,
    label: str,
) -> bool:
    """编辑密钥字段。

    Args:
        db: 配置对象。
        field: 字段名。
        label: 显示标签。

    Returns:
        bool: 是否修改成功。
    """
    new_val = await prompt_text(f"新的 {label}", is_password=True)
    if new_val is None:
        return False

    if not new_val:
        return False

    setattr(db, field, SecretStr(new_val))
    return True
