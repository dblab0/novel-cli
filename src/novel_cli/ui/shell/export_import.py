"""会话导出/导入命令模块。

提供 /export 和 /import 命令，用于导出当前会话上下文到文件，或从文件/会话 ID 导入上下文。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from kaos.path import KaosPath

from novel_cli.ui.shell.console import console
from novel_cli.ui.shell.slash import ensure_novel_soul, registry, shell_mode_registry
from novel_cli.utils.export import is_sensitive_file
from novel_cli.utils.path import sanitize_cli_path, shorten_home
from novel_cli.wire.types import TurnBegin, TurnEnd

if TYPE_CHECKING:
    from novel_cli.ui.shell import Shell


# ---------------------------------------------------------------------------
# /export 命令
# ---------------------------------------------------------------------------


@registry.command
@shell_mode_registry.command
async def export(app: Shell, args: str):
    """导出当前会话上下文到 Markdown 文件。

    Args:
        app: Shell 应用实例。
        args: 命令参数，可指定导出文件路径。
    """
    from novel_cli.utils.export import perform_export

    soul = ensure_novel_soul(app)
    if soul is None:
        return

    session = soul.runtime.session
    result = await perform_export(
        history=list(soul.context.history),
        session_id=session.id,
        work_dir=str(session.work_dir),
        token_count=soul.context.token_count,
        args=args,
        default_dir=Path(str(session.work_dir)),
    )
    if isinstance(result, str):
        console.print(f"[yellow]{result}[/yellow]")
        return

    output, count = result
    display = shorten_home(KaosPath(str(output)))
    console.print(f"[green]Exported {count} messages to {display}[/green]")
    console.print(
        "[yellow]Note: The exported file may contain sensitive information. "
        "Please be cautious when sharing it externally.[/yellow]"
    )


# ---------------------------------------------------------------------------
# /import 命令
# ---------------------------------------------------------------------------


@registry.command(name="import")
@shell_mode_registry.command(name="import")
async def import_context(app: Shell, args: str):
    """从文件或会话 ID 导入上下文。

    Args:
        app: Shell 应用实例。
        args: 命令参数，指定导入源（文件路径或会话 ID）。
    """
    from novel_cli.utils.export import perform_import

    soul = ensure_novel_soul(app)
    if soul is None:
        return

    target = sanitize_cli_path(args)
    if not target:
        console.print("[yellow]Usage: /import <file_path or session_id>[/yellow]")
        return

    session = soul.runtime.session
    raw_max_context_size = (
        soul.runtime.llm.max_context_size if soul.runtime.llm is not None else None
    )
    max_context_size = (
        raw_max_context_size
        if isinstance(raw_max_context_size, int) and raw_max_context_size > 0
        else None
    )
    result = await perform_import(
        target=target,
        current_session_id=session.id,
        work_dir=session.work_dir,
        context=soul.context,
        max_context_size=max_context_size,
    )
    if isinstance(result, str):
        console.print(f"[red]{result}[/red]")
        return

    source_desc, content_len = result

    # 写入 wire 文件，使导入在会话回放中显示
    await soul.wire_file.append_message(
        TurnBegin(user_input=f"[Imported context from {source_desc}]")
    )
    await soul.wire_file.append_message(TurnEnd())

    console.print(
        f"[green]Imported context from {source_desc} "
        f"({content_len} chars) into current session.[/green]"
    )
    if source_desc.startswith("file") and is_sensitive_file(Path(target).name):
        console.print(
            "[yellow]Warning: This file may contain secrets (API keys, tokens, credentials). "
            "The content is now part of your session context.[/yellow]"
        )
