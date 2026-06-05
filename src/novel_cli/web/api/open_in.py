"""在本地应用程序中打开主机路径的 API 路由。

本模块提供在 macOS 和 Windows 平台上使用本地应用程序打开文件或目录的功能。
支持的程序包括：Finder/资源管理器、Cursor、VSCode、iTerm/Terminal 等。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from novel_cli import logger

router = APIRouter(prefix="/api/open-in", tags=["open-in"])


class OpenInRequest(BaseModel):
    """打开路径请求模型。

    Attributes:
        app: 要使用的应用程序名称。
        path: 要打开的路径字符串。
    """

    app: Literal["finder", "cursor", "vscode", "iterm", "terminal", "antigravity"]
    path: str


class OpenInResponse(BaseModel):
    """打开路径响应模型。

    Attributes:
        ok: 操作是否成功。
        detail: 错误详情，成功时为 None。
    """

    ok: bool
    detail: str | None = None


def _resolve_path(path: str) -> Path:
    """解析并验证路径。

    Args:
        path: 要解析的路径字符串。

    Returns:
        解析后的 Path 对象。

    Raises:
        HTTPException: 路径不存在时抛出 400 错误。
    """
    resolved = Path(path).expanduser()
    try:
        resolved = resolved.resolve()
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path does not exist: {path}",
        ) from None

    if not resolved.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path does not exist: {path}",
        )
    return resolved


def _run_command(args: list[str]) -> None:
    """同步执行命令并等待完成。

    Args:
        args: 命令参数列表。
    """
    subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    )


def _spawn_process(args: list[str]) -> None:
    """启动子进程但不等待其完成。

    Args:
        args: 命令参数列表。
    """
    subprocess.Popen(args, close_fds=True)


def _open_app(app_name: str, path: Path, fallback: str | None = None) -> None:
    """使用 macOS open 命令打开应用程序。

    Args:
        app_name: 应用程序名称。
        path: 要打开的路径。
        fallback: 备选应用程序名称，主应用失败时使用。
    """
    try:
        _run_command(["open", "-a", app_name, str(path)])
        return
    except subprocess.CalledProcessError as exc:
        if fallback is None:
            raise
        logger.warning("Open with {} failed: {}", app_name, exc)
    _run_command(["open", "-a", fallback, str(path)])


def _open_terminal(path: Path) -> None:
    """使用 AppleScript 打开 macOS Terminal 并切换到指定目录。

    Args:
        path: 目标目录路径。
    """
    script = f'tell application "Terminal" to do script "cd " & quoted form of "{path}"'
    _run_command(["osascript", "-e", script])


def _open_iterm(path: Path) -> None:
    """使用 AppleScript 打开 iTerm 并切换到指定目录。

    Args:
        path: 目标目录路径。
    """
    script = "\n".join(
        [
            'tell application "iTerm"',
            "  create window with default profile",
            "  tell current session of current window",
            f'    write text "cd " & quoted form of "{path}"',
            "  end tell",
            "end tell",
        ]
    )
    try:
        _run_command(["osascript", "-e", script])
    except subprocess.CalledProcessError:
        script = script.replace('"iTerm"', '"iTerm2"')
        _run_command(["osascript", "-e", script])


def _open_windows_app(command: str, path: Path) -> None:
    """在 Windows 上打开应用程序。

    Args:
        command: 应用程序命令名。
        path: 要打开的路径。
    """
    _run_command(["cmd", "/c", "start", "", command, str(path)])


def _open_windows_explorer(path: Path, *, is_file: bool) -> None:
    """使用 Windows 资源管理器打开路径。

    Args:
        path: 目标路径。
        is_file: 是否为文件，文件时会选中该文件。
    """
    if is_file:
        _spawn_process(["explorer", f"/select,{path}"])
    else:
        _spawn_process(["explorer", str(path)])


def _open_windows_terminal(path: Path) -> None:
    """打开 Windows Terminal 并切换到指定目录。

    如果 Windows Terminal 不可用，则回退到 cmd.exe。

    Args:
        path: 目标目录路径。
    """
    try:
        _run_command(["cmd", "/c", "start", "", "wt.exe", "-d", str(path)])
    except subprocess.CalledProcessError as exc:
        logger.warning("Open with Windows Terminal failed: {}", exc)
        _run_command(["cmd", "/c", "start", "", "cmd.exe", "/K", f'cd /d "{path}"'])


def _open_in_macos(app: OpenInRequest, path: Path, *, is_file: bool) -> None:
    """在 macOS 上打开应用程序。

    Args:
        app: 打开请求对象。
        path: 目标路径。
        is_file: 是否为文件。

    Raises:
        HTTPException: 应用程序不支持时抛出 400 错误。
    """
    match app.app:
        case "finder":
            if is_file:
                # 在 Finder 中显示文件
                _run_command(["open", "-R", str(path)])
            else:
                _run_command(["open", str(path)])
        case "cursor":
            _open_app("Cursor", path)
        case "vscode":
            _open_app("Visual Studio Code", path, fallback="Code")
        case "antigravity":
            _open_app("Antigravity", path)
        case "iterm":
            # 终端程序需要目录路径
            directory = path.parent if is_file else path
            _open_iterm(directory)
        case "terminal":
            directory = path.parent if is_file else path
            _open_terminal(directory)
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported app: {app.app}",
            )


def _open_in_windows(app: OpenInRequest, path: Path, *, is_file: bool) -> None:
    """在 Windows 上打开应用程序。

    Args:
        app: 打开请求对象。
        path: 目标路径。
        is_file: 是否为文件。

    Raises:
        HTTPException: 应用程序不支持时抛出 400 错误。
    """
    match app.app:
        case "finder":
            _open_windows_explorer(path, is_file=is_file)
        case "cursor":
            _open_windows_app("cursor", path)
        case "vscode":
            _open_windows_app("code", path)
        case "terminal":
            directory = path.parent if is_file else path
            _open_windows_terminal(directory)
        case "iterm" | "antigravity":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{app.app} is not supported on Windows.",
            )
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported app: {app.app}",
            )


def _open_in_sync(request: OpenInRequest, path: Path, *, is_file: bool) -> None:
    """同步执行打开操作。

    根据当前平台选择对应的打开方法。

    Args:
        request: 打开请求对象。
        path: 目标路径。
        is_file: 是否为文件。
    """
    if sys.platform == "darwin":
        _open_in_macos(request, path, is_file=is_file)
    else:
        _open_in_windows(request, path, is_file=is_file)


@router.post("", summary="在本地应用程序中打开路径")
async def open_in(request: OpenInRequest) -> OpenInResponse:
    """在本地应用程序中打开指定路径。

    Args:
        request: 包含应用程序名称和路径的请求对象。

    Returns:
        打开操作的响应结果。

    Raises:
        HTTPException: 平台不支持或打开失败时抛出相应错误。
    """
    if sys.platform not in {"darwin", "win32"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Open-in is only supported on macOS and Windows.",
        )

    path = _resolve_path(request.path)
    is_file = path.is_file()

    try:
        await asyncio.to_thread(_open_in_sync, request, path, is_file=is_file)
    except subprocess.CalledProcessError as exc:
        logger.warning("Open-in failed ({}): {}", request.app, exc)
        detail = exc.stderr.strip() if exc.stderr else "Failed to open application."
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        ) from exc

    return OpenInResponse(ok=True)
