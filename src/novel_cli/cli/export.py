"""导出命令模块。

用于打包会话数据为 ZIP 压缩包。
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from kaos.path import KaosPath

from novel_cli.wire.file import WireFileMetadata, parse_wire_file_line
from novel_cli.wire.types import TurnBegin

if TYPE_CHECKING:
    from novel_cli.session import Session

cli = typer.Typer(help="导出会话数据。")


async def _find_session_in_work_dir(work_dir: KaosPath, session_id: str) -> Session | None:
    """在工作目录中查找指定 ID 的会话。

    Args:
        work_dir: 工作目录路径。
        session_id: 会话 ID。

    Returns:
        找到的会话对象，如果未找到则返回 None。
    """
    from novel_cli.session import Session

    return await Session.find(work_dir, session_id)


async def _load_previous_session(work_dir: KaosPath) -> Session | None:
    """加载工作目录的上一个会话。

    Args:
        work_dir: 工作目录路径。

    Returns:
        上一个会话对象，如果不存在则返回 None。
    """
    from novel_cli.session import Session

    return await Session.continue_(work_dir)


def _resolve_work_dir(ctx: typer.Context) -> KaosPath:
    """从上下文中解析工作目录。

    Args:
        ctx: Typer 上下文对象。

    Returns:
        解析后的工作目录路径。
    """
    root_ctx = ctx.find_root()
    local_work_dir = root_ctx.params.get("local_work_dir")
    if local_work_dir is None:
        return KaosPath.cwd()
    return KaosPath.unsafe_from_local_path(local_work_dir)


def _find_session_by_id(session_id: str, *, work_dir: KaosPath | None = None) -> Path | None:
    """通过 ID 查找会话目录，优先在当前工作目录中查找。

    Args:
        session_id: 会话 ID。
        work_dir: 工作目录路径，可选。

    Returns:
        会话目录路径，如果未找到则返回 None。
    """
    if work_dir is not None:
        session = asyncio.run(_find_session_in_work_dir(work_dir, session_id))
        if session is not None:
            return session.dir

    from novel_cli.share import get_share_dir

    sessions_root = get_share_dir() / "sessions"
    if not sessions_root.exists():
        return None

    for work_dir_hash_dir in sessions_root.iterdir():
        if not work_dir_hash_dir.is_dir():
            continue
        candidate = work_dir_hash_dir / session_id
        if candidate.is_dir():
            return candidate

    return None


def _last_user_message_timestamp(session_dir: Path) -> float | None:
    """获取会话中最后一条用户消息的时间戳。

    Args:
        session_dir: 会话目录路径。

    Returns:
        最后一条用户消息的时间戳，如果没有则返回 None。
    """
    wire_file = session_dir / "wire.jsonl"
    if not wire_file.exists():
        return None

    last_turn_begin: float | None = None
    try:
        with wire_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = parse_wire_file_line(line)
                except Exception:
                    continue
                if isinstance(parsed, WireFileMetadata):
                    continue
                if isinstance(parsed.to_wire_message(), TurnBegin):
                    last_turn_begin = parsed.timestamp
    except OSError:
        return None

    return last_turn_begin


def _format_message_timestamp(timestamp: float | None) -> str:
    if timestamp is None:
        return "(no user message)"
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _confirm_previous_session(session: Session) -> bool:
    """确认是否导出上一个会话。

    Args:
        session: 会话对象。

    Returns:
        用户确认是否导出。
    """
    last_user_message = _format_message_timestamp(_last_user_message_timestamp(session.dir))

    typer.echo("About to export the previous session for this working directory:")
    typer.echo()
    typer.echo(f"Work dir: {session.work_dir}")
    typer.echo(f"Session ID: {session.id}")
    typer.echo(f"Title: {session.title}")
    typer.echo(f"Last user message: {last_user_message}")
    typer.echo()
    return typer.confirm("Export this session?", default=False)


@cli.command(name="export")
def export(
    ctx: typer.Context,
    session_id: Annotated[
        str | None,
        typer.Argument(help="要导出的会话 ID。默认为上一个会话。"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="输出 ZIP 文件路径。默认为当前目录下的 session-{id}.zip。",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="导出上一个会话时跳过确认。",
        ),
    ] = False,
) -> None:
    """将会话导出为 ZIP 压缩包。

    Args:
        ctx: Typer 上下文对象。
        session_id: 要导出的会话 ID。
        output: 输出 ZIP 文件路径。
        yes: 是否跳过确认。

    Raises:
        typer.Exit: 当会话未找到或无法导出时。
    """
    work_dir = _resolve_work_dir(ctx)

    if session_id is None:
        session = asyncio.run(_load_previous_session(work_dir))
        if session is None:
            typer.echo("Error: no previous session found for the working directory.", err=True)
            raise typer.Exit(code=1)
        if not yes and not _confirm_previous_session(session):
            typer.echo("Export cancelled.")
            return
        session_id = session.id
        session_dir = session.dir
    else:
        session_dir = _find_session_by_id(session_id, work_dir=work_dir)
        if session_dir is None:
            typer.echo(f"Error: session '{session_id}' not found.", err=True)
            raise typer.Exit(code=1)

    # 收集文件
    files = sorted(f for f in session_dir.iterdir() if f.is_file())
    if not files:
        typer.echo(f"Error: session '{session_id}' has no files.", err=True)
        raise typer.Exit(code=1)

    # 确定输出路径
    if output is None:
        output = Path.cwd() / f"session-{session_id}.zip"

    # 创建 ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            zf.write(file_path, arcname=file_path.name)
    buf.seek(0)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(buf.getvalue())

    typer.echo(str(output))
