"""会话 API 路由模块。

本模块提供 Novel CLI web 界面的会话管理 API，包括：
- 会话的创建、查询、更新、删除操作
- 会话历史记录回放
- 文件上传与管理
- WebSocket 实时通信
- git 差异统计
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from kaos.path import KaosPath
from pydantic import BaseModel, Field
from starlette.websockets import WebSocket, WebSocketDisconnect

from novel_cli import logger
from novel_cli.metadata import load_metadata, save_metadata
from novel_cli.session import Session as NovelCLISession
from novel_cli.utils.subprocess_env import get_clean_env
from novel_cli.web.auth import is_origin_allowed, is_private_ip, verify_token
from novel_cli.web.models import (
    GenerateTitleRequest,
    GenerateTitleResponse,
    GitDiffStats,
    GitFileDiff,
    Session,
    SessionStatus,
    UpdateSessionRequest,
)
from novel_cli.web.runner.messages import new_session_status_message, send_history_complete
from novel_cli.web.runner.process import NovelCLIRunner
from novel_cli.web.store.sessions import (
    JointSession,
    invalidate_sessions_cache,
    load_session_by_id,
    load_sessions_page,
    run_auto_archive,
)
from novel_cli.wire.jsonrpc import (
    ErrorCodes,
    JSONRPCErrorObject,
    JSONRPCErrorResponse,
    JSONRPCInMessageAdapter,
    JSONRPCPromptMessage,
)
from novel_cli.wire.serde import deserialize_wire_message
from novel_cli.wire.types import is_request

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
work_dirs_router = APIRouter(prefix="/api/work-dirs", tags=["work-dirs"])
defaults_router = APIRouter(prefix="/api", tags=["defaults"])
books_router = APIRouter(prefix="/api", tags=["books"])

# 常量定义
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 最大上传文件大小：100MB
DEFAULT_MAX_PUBLIC_PATH_DEPTH = 6  # 公共访问路径的最大深度
# 敏感路径组成部分（文件名或目录名）
SENSITIVE_PATH_PARTS = {
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    "credentials",
    ".aws",
    ".ssh",
    ".gnupg",
    ".kube",
    ".npmrc",
    ".pypirc",
    ".netrc",
}
# 敏感路径扩展名（证书、密钥等）
SENSITIVE_PATH_EXTENSIONS = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".kdbx",
    ".der",
}
# 家目录敏感路径模式，用于检测解析路径是否逃逸到敏感位置
SENSITIVE_HOME_PATHS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
}


def sanitize_filename(filename: str) -> str:
    """移除文件名中潜在的危险字符。

    Args:
        filename: 原始文件名。

    Returns:
        经过清理的安全文件名。如果清理后为空字符串，则返回 "unnamed"。
    """
    # 仅保留字母数字、点、下划线、连字符和空格
    safe = "".join(c for c in filename if c.isalnum() or c in "._- ")
    return safe.strip() or "unnamed"


def get_runner(req: Request) -> NovelCLIRunner:
    """从 FastAPI 应用状态中获取 NovelCLIRunner。

    Args:
        req: FastAPI 请求对象。

    Returns:
        NovelCLIRunner 实例。
    """
    return req.app.state.runner


def get_runner_ws(ws: WebSocket) -> NovelCLIRunner:
    """从 FastAPI 应用状态中获取 NovelCLIRunner（用于 WebSocket 路由）。

    Args:
        ws: WebSocket 连接对象。

    Returns:
        NovelCLIRunner 实例。
    """
    return ws.app.state.runner


def get_editable_session(
    session_id: UUID,
    runner: NovelCLIRunner,
) -> JointSession:
    """获取会话并验证其是否处于可编辑状态（非忙碌状态）。

    Args:
        session_id: 会话 ID。
        runner: NovelCLIRunner 实例。

    Returns:
        可编辑的会话对象。

    Raises:
        HTTPException: 如果会话不存在（404）或会话处于忙碌状态（400）。
    """
    session = load_session_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    # 检查会话是否处于忙碌状态
    session_process = runner.get_session(session_id)
    if session_process and session_process.is_busy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session is busy. Please wait for it to complete before modifying.",
        )
    return session


def _relative_parts(path: Path) -> list[str]:
    return [part for part in path.parts if part not in {"", "."}]


def _is_sensitive_relative_path(rel_path: Path) -> bool:
    parts = _relative_parts(rel_path)
    for part in parts:
        if part.startswith("."):
            return True
        if part.lower() in SENSITIVE_PATH_PARTS:
            return True
    return rel_path.suffix.lower() in SENSITIVE_PATH_EXTENSIONS


def _contains_symlink(path: Path, base: Path) -> bool:
    """检查路径（相对于基准路径）的任何组成部分是否为符号链接。

    Args:
        path: 要检查的路径。
        base: 基准路径。

    Returns:
        如果路径中存在符号链接则返回 True，否则返回 False。
        如果发生 ValueError 或 OSError，返回 True（视为不安全）。
    """
    try:
        current = base
        rel_parts = path.relative_to(base).parts
        for part in rel_parts:
            current = current / part
            if current.is_symlink():
                return True
    except (ValueError, OSError):
        return True
    return False


def _is_path_in_sensitive_location(path: Path) -> bool:
    """检查解析后的路径是否指向敏感位置（如 ~/.ssh、~/.aws）。

    Args:
        path: 要检查的路径。

    Returns:
        如果路径指向敏感位置则返回 True，否则返回 False。
    """
    try:
        home = Path.home()
        if path.is_relative_to(home):
            rel_to_home = path.relative_to(home)
            first_part = rel_to_home.parts[0] if rel_to_home.parts else ""
            if first_part in SENSITIVE_HOME_PATHS:
                return True
    except (ValueError, RuntimeError):
        pass
    return False


def _ensure_public_file_access_allowed(
    rel_path: Path,
    restrict_sensitive_apis: bool,
    max_path_depth: int = DEFAULT_MAX_PUBLIC_PATH_DEPTH,
) -> None:
    if not restrict_sensitive_apis:
        return
    rel_parts = _relative_parts(rel_path)
    if len(rel_parts) > max_path_depth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Path too deep for public access "
            f"(max depth: {max_path_depth}, current: {len(rel_parts)}).",
        )
    if _is_sensitive_relative_path(rel_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to sensitive files is disabled.",
        )


def _read_wire_lines(wire_file: Path) -> list[str]:
    """读取并解析 wire.jsonl 文件为 JSONRPC 事件字符串列表（在线程中运行）。

    Args:
        wire_file: wire.jsonl 文件路径。

    Returns:
        解析后的 JSONRPC 事件字符串列表。
    """
    result: list[str] = []
    with open(wire_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                record = cast(dict[str, Any], record)
                record_type = record.get("type")
                if isinstance(record_type, str) and record_type == "metadata":
                    continue
                message_raw = record.get("message")
                if not isinstance(message_raw, dict):
                    continue
                message_raw = cast(dict[str, Any], message_raw)
                message = deserialize_wire_message(message_raw)
                _is_req = is_request(message)
                event_msg: dict[str, Any] = {
                    "jsonrpc": "2.0",
                    "method": "request" if _is_req else "event",
                    "params": message_raw,
                }
                if _is_req:
                    # JSON-RPC 请求需要顶层 ``id`` 字段，以便客户端关联其响应。
                    # 使用请求自身的 ``id`` 字段（如 ApprovalRequest.id、QuestionRequest.id）。
                    # 注意：``message_raw`` 将数据封装为 ``{"type": ..., "payload": {...}}``，
                    # 因此 id 位于反序列化对象上，而非原始字典顶层。
                    event_msg["id"] = message.id
                result.append(json.dumps(event_msg, ensure_ascii=False))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                continue
    return result


async def replay_history(ws: WebSocket, session_dir: Path) -> None:
    """将 wire.jsonl 中的历史 wire 消息回放至 WebSocket。

    Args:
        ws: WebSocket 连接对象。
        session_dir: 会话目录路径。
    """
    wire_file = session_dir / "wire.jsonl"
    if not await asyncio.to_thread(wire_file.exists):
        return

    try:
        lines = await asyncio.to_thread(_read_wire_lines, wire_file)
        for event_text in lines:
            await ws.send_text(event_text)
    except Exception:
        pass


@router.get("/", summary="列出所有会话")
async def list_sessions(
    runner: NovelCLIRunner = Depends(get_runner),
    limit: int = 100,
    offset: int = 0,
    q: str | None = None,
    archived: bool | None = None,
) -> list[Session]:
    """列出会话，支持可选的分页和搜索。

    Args:
        limit: 返回的最大会话数量（默认 100，最大 500）。
        offset: 跳过的会话数量（默认 0）。
        q: 可选的搜索查询，用于按标题或工作目录过滤。
        archived: 按归档状态过滤。
            - None（默认）：仅返回未归档的会话。
            - True：仅返回已归档的会话。

    Returns:
        会话列表。
    """
    if limit <= 0:
        limit = 100
    if limit > 500:
        limit = 500
    if offset < 0:
        offset = 0

    # 在后台运行自动归档（内部有节流机制，最多每 5 分钟运行一次）
    await asyncio.to_thread(run_auto_archive)

    sessions = load_sessions_page(limit=limit, offset=offset, query=q, archived=archived)
    for session in sessions:
        session_process = runner.get_session(session.session_id)
        session.is_running = session_process is not None and session_process.is_running
        session.status = session_process.status if session_process else None
    return cast(list[Session], sessions)


@router.get("/{session_id}", summary="获取会话")
async def get_session(
    session_id: UUID,
    runner: NovelCLIRunner = Depends(get_runner),
) -> Session | None:
    """根据 ID 获取会话。

    Args:
        session_id: 会话 ID。
        runner: NovelCLIRunner 实例（通过依赖注入）。

    Returns:
        会话对象，如果不存在则返回 None。
    """
    session = load_session_by_id(session_id)
    if session is not None:
        session_process = runner.get_session(session_id)
        session.is_running = session_process is not None and session_process.is_running
        session.status = session_process.status if session_process else None
    return session


@router.post("/", summary="创建新会话")
async def create_session(
    http_request: Request,
    request: CreateSessionRequest | None = None,
) -> Session:
    """创建新会话。

    Args:
        http_request: FastAPI HTTP 请求对象，用于访问 app.state.defaults。
        request: 创建会话请求，可选。包含工作目录和书籍等参数。

    Returns:
        新创建的会话对象。

    Raises:
        HTTPException: 如果目录不存在（404）、权限不足（403）或路径不是目录（400）。
    """
    from novel_cli.web.defaults import WebDefaults

    defaults: WebDefaults = http_request.app.state.defaults

    # work_dir: API 参数 > CLI 默认（defaults.work_dir 永远非 None，由 CLI 层保证）
    if request and request.work_dir:
        work_dir_path = Path(request.work_dir).expanduser().resolve()
        # 验证目录是否存在
        if not work_dir_path.exists():
            if request.create_dir:
                # 自动创建目录
                try:
                    work_dir_path.mkdir(parents=True, exist_ok=True)
                except PermissionError as e:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Permission denied: cannot create directory {request.work_dir}",
                    ) from e
                except OSError as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to create directory: {e}",
                    ) from e
            else:
                # 返回 404 表示目录不存在
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Directory does not exist: {request.work_dir}",
                )
        if not work_dir_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path is not a directory: {request.work_dir}",
            )
        work_dir = KaosPath.unsafe_from_local_path(work_dir_path)
    else:
        work_dir = KaosPath.unsafe_from_local_path(Path(defaults.work_dir))

    # book_name: API 参数 > CLI 默认
    book_name = (request.book_name if request else None) or defaults.book_name

    novel_cli_session = await NovelCLISession.create(work_dir=work_dir)

    # 持久化 agent_file 和 current_book 到 session state
    if defaults.agent_file:
        novel_cli_session.state.agent_file = defaults.agent_file
    if book_name:
        novel_cli_session.state.current_book = book_name
    novel_cli_session.save_state()

    context_file = novel_cli_session.dir / "context.jsonl"
    invalidate_sessions_cache()
    invalidate_work_dirs_cache()
    return Session(
        session_id=UUID(novel_cli_session.id),
        title=novel_cli_session.title,
        last_updated=datetime.fromtimestamp(context_file.stat().st_mtime, tz=UTC),
        is_running=False,
        status=SessionStatus(
            session_id=UUID(novel_cli_session.id),
            state="stopped",
            seq=0,
            worker_id=None,
            reason=None,
            detail=None,
            updated_at=datetime.now(UTC),
        ),
        work_dir=str(work_dir),
        session_dir=str(novel_cli_session.dir),
    )


class CreateSessionRequest(BaseModel):
    """创建会话请求模型。

    Attributes:
        work_dir: 工作目录路径，可选。
        create_dir: 是否自动创建目录（如果不存在），默认 False。
        book_name: 书籍名称，可选。优先级高于 CLI 启动默认值。
    """

    work_dir: str | None = None
    create_dir: bool = False  # 是否自动创建目录（如果不存在）
    book_name: str | None = None


class ForkSessionRequest(BaseModel):
    """分支会话请求模型。

    Attributes:
        turn_index: 分支点索引（0 基），分支包含该轮及其之前的所有轮次。
    """

    turn_index: int = Field(..., ge=0)  # 0 基索引，分支包含该轮及其之前的所有轮次


class UploadSessionFileResponse(BaseModel):
    """上传文件响应模型。

    Attributes:
        path: 上传文件的完整路径。
        filename: 文件名。
        size: 文件大小（字节）。
    """

    path: str
    filename: str
    size: int


@router.post("/{session_id}/files", summary="上传文件到会话")
async def upload_session_file(
    session_id: UUID,
    file: UploadFile,
    runner: NovelCLIRunner = Depends(get_runner),
) -> UploadSessionFileResponse:
    """上传文件到会话。

    Args:
        session_id: 会话 ID。
        file: 上传的文件对象。
        runner: NovelCLIRunner 实例（通过依赖注入）。

    Returns:
        上传文件响应，包含路径、文件名和大小。

    Raises:
        HTTPException: 如果文件过大（413）。
    """
    session = get_editable_session(session_id, runner)
    session_dir = session.novel_cli_session.dir
    upload_dir = session_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 读取并验证文件大小
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)",
        )

    # 生成安全的文件名
    file_name = str(uuid4())
    if file.filename:
        safe_name = sanitize_filename(file.filename)
        name, ext = os.path.splitext(safe_name)
        file_name = f"{name}_{file_name[:6]}{ext}"

    upload_path = upload_dir / file_name
    upload_path.write_bytes(content)

    return UploadSessionFileResponse(
        path=str(upload_path),
        filename=file_name,
        size=len(content),
    )


@router.get(
    "/{session_id}/uploads/{path:path}",
    summary="获取会话上传目录中的文件",
)
async def get_session_upload_file(
    session_id: UUID,
    path: str,
) -> Response:
    """从会话的上传目录获取文件。

    Args:
        session_id: 会话 ID。
        path: 相对于上传目录的文件路径。

    Returns:
        文件响应。

    Raises:
        HTTPException: 如果会话不存在（404）、上传目录不存在（404）、路径无效（400）或文件不存在（404）。
    """
    session = load_session_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    uploads_dir = (session.novel_cli_session.dir / "uploads").resolve()
    if not uploads_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Uploads directory not found",
        )

    file_path = (uploads_dir / path).resolve()
    if not file_path.is_relative_to(uploads_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: path traversal not allowed",
        )

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    media_type, _ = mimetypes.guess_type(file_path.name)
    encoded_filename = quote(file_path.name, safe="")
    return FileResponse(
        file_path,
        media_type=media_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
        },
    )


@router.get(
    "/{session_id}/files/{path:path}",
    summary="获取工作目录中的文件或列出目录",
)
async def get_session_file(
    session_id: UUID,
    path: str,
    request: Request,
) -> Response:
    """从会话工作目录获取文件或列出目录内容。

    Args:
        session_id: 会话 ID。
        path: 相对于工作目录的文件路径。
        request: FastAPI 请求对象。

    Returns:
        文件响应或目录列表（JSON）。

    Raises:
        HTTPException: 如果会话不存在（404）、路径无效（400）、路径过深（403）、
            访问敏感文件（403）、包含符号链接（403）、访问敏感系统目录（403）或文件不存在（404）。
    """
    session = load_session_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # 安全检查：使用 resolve() 防止路径遍历攻击
    work_dir = Path(str(session.novel_cli_session.work_dir)).resolve()
    requested_path = work_dir / path
    file_path = requested_path.resolve()

    # 检查路径遍历
    if not file_path.is_relative_to(work_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: path traversal not allowed",
        )

    rel_path = file_path.relative_to(work_dir)
    restrict_sensitive_apis = getattr(request.app.state, "restrict_sensitive_apis", False)
    max_path_depth = (
        getattr(request.app.state, "max_public_path_depth", None) or DEFAULT_MAX_PUBLIC_PATH_DEPTH
    )

    # 限制敏感 API 时的额外安全检查
    if restrict_sensitive_apis:
        # 检查路径中的符号链接
        if _contains_symlink(requested_path, work_dir):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Symbolic links are not allowed in public mode.",
            )

        # 检查解析后的路径是否指向敏感位置
        if _is_path_in_sensitive_location(file_path):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access to sensitive system directories is not allowed.",
            )

    _ensure_public_file_access_allowed(rel_path, restrict_sensitive_apis, max_path_depth)

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    if file_path.is_dir():
        result: list[dict[str, str | int]] = []
        for subpath in file_path.iterdir():
            if restrict_sensitive_apis:
                rel_subpath = rel_path / subpath.name
                if _is_sensitive_relative_path(rel_subpath):
                    continue
            if subpath.is_dir():
                result.append({"name": subpath.name, "type": "directory"})
            else:
                result.append(
                    {
                        "name": subpath.name,
                        "type": "file",
                        "size": subpath.stat().st_size,
                    }
                )
        result.sort(key=lambda x: (cast(str, x["type"]), cast(str, x["name"])))
        return Response(content=json.dumps(result), media_type="application/json")

    content = file_path.read_bytes()
    media_type, _ = mimetypes.guess_type(file_path.name)
    encoded_filename = quote(file_path.name, safe="")
    return Response(
        content=content,
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


def _update_last_session_id(session: JointSession) -> None:
    """更新会话工作目录的 last_session_id。

    Args:
        session: 会话对象。
    """
    novel_session = session.novel_cli_session
    work_dir = novel_session.work_dir

    metadata = load_metadata()
    work_dir_meta = metadata.get_work_dir_meta(work_dir)

    if work_dir_meta is None:
        work_dir_meta = metadata.new_work_dir_meta(work_dir)

    work_dir_meta.last_session_id = novel_session.id
    save_metadata(metadata)


@router.delete("/{session_id}", summary="删除会话")
async def delete_session(session_id: UUID, runner: NovelCLIRunner = Depends(get_runner)) -> None:
    """删除会话。

    Args:
        session_id: 会话 ID。
        runner: NovelCLIRunner 实例（通过依赖注入）。

    Raises:
        HTTPException: 如果会话不存在（404）或会话处于忙碌状态（400）。
    """
    session = get_editable_session(session_id, runner)
    session_process = runner.get_session(session_id)
    if session_process is not None:
        await session_process.stop()
    wd_meta = session.novel_cli_session.work_dir_meta
    if wd_meta.last_session_id == str(session_id):
        metadata = load_metadata()
        for wd in metadata.work_dirs:
            if wd.path == wd_meta.path:
                wd.last_session_id = None
                break
        save_metadata(metadata)
    session_dir = session.novel_cli_session.dir
    if session_dir.exists():
        shutil.rmtree(session_dir)
    invalidate_sessions_cache()


@router.patch("/{session_id}", summary="更新会话")
async def update_session(
    session_id: UUID,
    request: UpdateSessionRequest,
    runner: NovelCLIRunner = Depends(get_runner),
) -> Session:
    """更新会话（如重命名标题或归档/取消归档）。

    Args:
        session_id: 会话 ID。
        request: 更新会话请求。
        runner: NovelCLIRunner 实例（通过依赖注入）。

    Returns:
        更新后的会话对象。

    Raises:
        HTTPException: 如果会话不存在（404）、会话处于忙碌状态（400）或重新加载失败（500）。
    """
    from novel_cli.session_state import load_session_state, save_session_state

    session = get_editable_session(session_id, runner)
    session_dir = session.novel_cli_session.dir
    state = load_session_state(session_dir)

    # 如果提供了标题则更新
    if request.title is not None:
        state.custom_title = request.title
        state.title_generated = True

    # 如果提供了归档状态则更新
    if request.archived is not None:
        state.archived = request.archived
        if request.archived:
            state.archived_at = time.time()
            state.auto_archive_exempt = False
        else:
            state.archived_at = None
            state.auto_archive_exempt = True

    save_session_state(state, session_dir)

    # 使缓存失效以强制重新加载
    invalidate_sessions_cache()

    # 返回更新后的会话
    updated_session = load_session_by_id(session_id)
    if updated_session is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reload session after update",
        )
    return updated_session


def extract_first_turn_from_wire(session_dir: Path) -> tuple[str, str] | None:
    """从 wire.jsonl 中提取第一轮的用户消息和助手响应。

    Args:
        session_dir: 会话目录路径。

    Returns:
        元组 (user_message, assistant_response)，如果未找到则返回 None。
    """
    wire_file = session_dir / "wire.jsonl"
    if not wire_file.exists():
        return None

    user_message: str | None = None
    assistant_response_parts: list[str] = []
    in_first_turn = False

    try:
        with open(wire_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    message = record.get("message", {})
                    msg_type = message.get("type")

                    if msg_type == "TurnBegin":
                        if in_first_turn:
                            # 第二轮已开始，停止
                            break
                        in_first_turn = True
                        user_input = message.get("payload", {}).get("user_input")
                        if user_input:
                            from kosong.message import Message

                            msg = Message(role="user", content=user_input)
                            user_message = msg.extract_text(" ")

                    elif msg_type == "ContentPart" and in_first_turn:
                        payload = message.get("payload", {})
                        if payload.get("type") == "text" and payload.get("text"):
                            assistant_response_parts.append(payload["text"])

                    elif msg_type == "TurnEnd" and in_first_turn:
                        break

                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    if user_message and assistant_response_parts:
        return (user_message, "".join(assistant_response_parts))
    return None


@router.post("/{session_id}/fork", summary="在指定轮次分支会话")
async def fork_session_endpoint(
    session_id: UUID,
    request: ForkSessionRequest,
    runner: NovelCLIRunner = Depends(get_runner),
) -> Session:
    """分支会话，创建一个包含指定轮次之前历史的新会话。

    新会话与原会话共享相同的工作目录。

    Args:
        session_id: 源会话 ID。
        request: 分支会话请求，包含分支点索引。
        runner: NovelCLIRunner 实例（通过依赖注入）。

    Returns:
        新创建的分支会话对象。

    Raises:
        HTTPException: 如果分支操作失败（400）。
    """
    from novel_cli.session_fork import fork_session as do_fork

    source_session = get_editable_session(session_id, runner)
    source_dir = source_session.novel_cli_session.dir
    work_dir = source_session.novel_cli_session.work_dir

    source_title = source_session.title

    try:
        new_session_id = await do_fork(
            source_session_dir=source_dir,
            work_dir=work_dir,
            turn_index=request.turn_index,
            title_prefix="Fork",
            source_title=source_title,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    invalidate_sessions_cache()
    invalidate_work_dirs_cache()

    from novel_cli.metadata import load_metadata
    from novel_cli.session_state import load_session_state

    metadata = load_metadata()
    work_dir_meta = metadata.get_work_dir_meta(work_dir)
    assert work_dir_meta is not None
    new_session_dir = work_dir_meta.sessions_dir / new_session_id
    new_state = load_session_state(new_session_dir)
    fork_title = new_state.custom_title or f"Fork: {source_title}"

    context_file = new_session_dir / "context.jsonl"
    return Session(
        session_id=UUID(new_session_id),
        title=fork_title,
        last_updated=datetime.fromtimestamp(context_file.stat().st_mtime, tz=UTC),
        is_running=False,
        status=SessionStatus(
            session_id=UUID(new_session_id),
            state="stopped",
            seq=0,
            worker_id=None,
            reason=None,
            detail=None,
            updated_at=datetime.now(UTC),
        ),
        work_dir=str(work_dir),
        session_dir=str(new_session_dir),
    )


@router.post("/{session_id}/generate-title", summary="使用 AI 生成会话标题")
async def generate_session_title(
    session_id: UUID,
    request: GenerateTitleRequest | None = None,
    runner: NovelCLIRunner = Depends(get_runner),
) -> GenerateTitleResponse:
    """基于首轮对话内容使用 AI 生成简洁的会话标题。

    如果请求体为空或参数缺失，后端将自动从 wire.jsonl 读取首轮内容。

    Args:
        session_id: 会话 ID。
        request: 生成标题请求，可选。包含用户消息和助手响应。
        runner: NovelCLIRunner 实例（通过依赖注入）。

    Returns:
        生成的标题响应。
    """
    session = get_editable_session(session_id, runner)
    session_dir = session.novel_cli_session.dir

    from novel_cli.session_state import load_session_state, save_session_state

    state = load_session_state(session_dir)

    # 检查标题是否已生成（避免重复调用）
    if state.title_generated:
        return GenerateTitleResponse(title=state.custom_title or "Untitled")

    # 获取消息内容：优先使用请求参数，否则从 wire.jsonl 读取
    user_message = request.user_message if request else None
    assistant_response = request.assistant_response if request else None

    if not user_message or not assistant_response:
        first_turn = extract_first_turn_from_wire(session_dir)
        if first_turn:
            user_message, assistant_response = first_turn

    # 如果仍然没有用户消息，返回默认标题
    if not user_message:
        return GenerateTitleResponse(title="Untitled")

    from novel_cli.utils.string import shorten

    user_text = user_message.strip()
    user_text = " ".join(user_text.split())
    fallback_title = shorten(user_text, width=50) or "Untitled"

    # 如果 AI 生成失败次数过多，使用回退标题并标记为已生成
    if state.title_generate_attempts >= 3:
        fresh = load_session_state(session_dir)
        fresh.custom_title = fallback_title
        fresh.title_generated = True
        save_session_state(fresh, session_dir)
        invalidate_sessions_cache()
        return GenerateTitleResponse(title=fallback_title)

    # 尝试使用 AI 生成标题
    title = fallback_title
    ai_generated = False
    try:
        from kosong import generate
        from kosong.message import Message

        from novel_cli.config import load_config
        from novel_cli.llm import create_llm

        config = load_config()
        model_name = config.default_model

        if model_name and model_name in config.models:
            model_config = config.models[model_name]
            provider_config = config.providers.get(model_config.provider)

            if provider_config:
                llm = create_llm(provider_config, model_config)

                if llm:
                    system_prompt = (
                        "Generate a concise session title (max 50 characters) "
                        "based on the conversation. "
                        "Only respond with the title text, nothing else. "
                        "No quotes, no explanation."
                    )

                    prompt = f"""User: {user_message[:300]}
Assistant: {(assistant_response or "")[:300]}

Title:"""

                    result = await generate(
                        chat_provider=llm.chat_provider,
                        system_prompt=system_prompt,
                        tools=[],
                        history=[Message(role="user", content=prompt)],
                    )

                    generated_title = result.message.extract_text().strip()
                    # 移除引号（如果存在）
                    generated_title = generated_title.strip("\"'")

                    if generated_title and len(generated_title) <= 50:
                        title = generated_title
                        ai_generated = True
                    elif generated_title:
                        title = shorten(generated_title, width=50)
                        ai_generated = True

    except Exception as e:
        logger.warning(f"Failed to generate title using AI: {e}")
        # 保持回退标题，ai_generated 保持 False

    # 读取-修改-写入：重新加载最新状态以避免覆盖
    # 在 LLM 调用期间 worker 进行的更改
    fresh = load_session_state(session_dir)
    fresh.custom_title = title
    if ai_generated:
        fresh.title_generated = True
    else:
        fresh.title_generate_attempts = fresh.title_generate_attempts + 1
    save_session_state(fresh, session_dir)

    # 使缓存失效
    invalidate_sessions_cache()

    return GenerateTitleResponse(title=title)


@router.websocket("/{session_id}/stream")
async def session_stream(
    session_id: UUID,
    websocket: WebSocket,
    runner: NovelCLIRunner = Depends(get_runner_ws),
) -> None:
    """会话的 WebSocket 流式接口。

    流程：
    1. 接受 WebSocket 连接
    2. 如果存在历史记录，以回放模式附加 WebSocket
    3. 从 wire.jsonl 回放历史消息
    4. 如有需要则启动 worker
    5. 刷新缓冲的实时消息并发送状态快照
    6. 将传入消息转发到子进程
    7. 断开连接时清理

    Args:
        session_id: 会话 ID。
        websocket: WebSocket 连接对象。
        runner: NovelCLIRunner 实例（通过依赖注入）。
    """
    expected_token = getattr(websocket.app.state, "session_token", None)
    enforce_origin = getattr(websocket.app.state, "enforce_origin", False)
    allowed_origins = getattr(websocket.app.state, "allowed_origins", [])
    lan_only = getattr(websocket.app.state, "lan_only", False)

    # 仅局域网访问检查
    if lan_only:
        client_ip = websocket.client.host if websocket.client else None
        if client_ip and not is_private_ip(client_ip):
            await websocket.close(code=4403, reason="Access denied: LAN only")
            return

    if enforce_origin:
        origin = websocket.headers.get("origin")
        if origin and not is_origin_allowed(origin, allowed_origins):
            await websocket.close(code=4403, reason="Origin not allowed")
            return

    if expected_token:
        token = websocket.query_params.get("token")
        if not verify_token(token, expected_token):
            await websocket.close(code=4401, reason="Auth required")
            return

    await websocket.accept()

    # 检查会话是否存在
    session = await asyncio.to_thread(load_session_by_id, session_id)
    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    # 检查会话是否有历史记录
    session_dir = session.novel_cli_session.dir
    wire_file = session_dir / "wire.jsonl"
    has_history = await asyncio.to_thread(wire_file.exists)

    session_process = await runner.get_or_create_session(session_id)
    attached = False
    try:
        if has_history:
            # 在历史回放前以回放模式附加 WebSocket
            await session_process.add_websocket_and_begin_replay(websocket)
            attached = True

            # 回放历史
            try:
                await replay_history(websocket, session_dir)
            except Exception as e:
                logger.warning(f"Failed to replay history: {e}")

        # 在继续之前检查 WebSocket 是否仍连接
        if not await send_history_complete(websocket):
            logger.debug("WebSocket disconnected during history replay")
            return

        # 启动会话环境——如果此处失败，发送错误状态
        # 以便客户端不会卡在"正在连接环境..."
        try:
            # 确保工作目录存在
            work_dir = Path(str(session.novel_cli_session.work_dir))
            await asyncio.to_thread(lambda: work_dir.mkdir(parents=True, exist_ok=True))

            if not attached:
                # 无历史记录：附加并启动 worker
                session_process = await runner.get_or_create_session(session_id)
                await session_process.add_websocket_and_begin_replay(websocket)
                attached = True

            assert session_process is not None
            # 结束回放并启动 worker
            await session_process.end_replay(websocket)
            await session_process.start()
            await session_process.send_status_snapshot(websocket)
        except Exception as e:
            logger.warning(f"Failed to start session environment: {e}")
            try:
                error_status = SessionStatus(
                    session_id=session_id,
                    state="error",
                    seq=0,
                    worker_id=None,
                    reason="initialization_failed",
                    detail=str(e),
                    updated_at=datetime.now(UTC),
                )
                await websocket.send_text(
                    new_session_status_message(error_status).model_dump_json()
                )
            except Exception:
                pass
            return

        # 跟踪是否已为此连接更新 last_session_id。
        # 我们将更新推迟到第一条 prompt 消息实际转发时，
        # 这样仅打开/查看会话不会更改 last_session_id。
        last_session_id_updated = False

        # 将传入消息转发到子进程
        while True:
            try:
                message = await websocket.receive_text()
                # 会话忙碌时拒绝新的 prompt
                if session_process.is_busy:
                    try:
                        in_message = JSONRPCInMessageAdapter.validate_json(message)
                    except ValueError:
                        in_message = None
                    if isinstance(in_message, JSONRPCPromptMessage):
                        await websocket.send_text(
                            JSONRPCErrorResponse(
                                id=in_message.id,
                                error=JSONRPCErrorObject(
                                    code=ErrorCodes.INVALID_STATE,
                                    message=(
                                        "Session is busy; wait for completion before sending "
                                        "a new prompt."
                                    ),
                                ),
                            ).model_dump_json()
                        )
                        continue

                # 在首次成功 prompt 时更新 last_session_id
                if not last_session_id_updated:
                    try:
                        in_message = JSONRPCInMessageAdapter.validate_json(message)
                    except ValueError:
                        in_message = None
                    if isinstance(in_message, JSONRPCPromptMessage):
                        await asyncio.to_thread(_update_last_session_id, session)
                        last_session_id_updated = True

                logger.debug(f"sending message to session {session_id}")
                await session_process.send_message(message)
            except WebSocketDisconnect:
                logger.debug("WebSocket disconnected")
                break
            except Exception as e:
                logger.warning(f"WebSocket error: {e.__class__.__name__} {e}")
                break
    finally:
        if attached and session_process:
            await session_process.remove_websocket(websocket)


# 工作目录缓存
_work_dirs_cache: list[str] | None = None
_work_dirs_cache_time: float = 0.0
_WORK_DIRS_CACHE_TTL = 30.0  # 缓存有效期（秒）


def invalidate_work_dirs_cache() -> None:
    """清除工作目录缓存。"""
    global _work_dirs_cache, _work_dirs_cache_time
    _work_dirs_cache = None
    _work_dirs_cache_time = 0.0


def _get_work_dirs_sync() -> list[str]:
    """获取工作目录列表的同步辅助函数（在线程池中运行）。

    Returns:
        工作目录路径列表（最多 20 个）。
    """
    import time

    global _work_dirs_cache, _work_dirs_cache_time

    # 检查缓存
    now = time.time()
    if _work_dirs_cache is not None and (now - _work_dirs_cache_time) < _WORK_DIRS_CACHE_TTL:
        return _work_dirs_cache

    # 构建最新列表
    metadata = load_metadata()
    work_dirs: list[str] = []
    for wd in metadata.work_dirs:
        # 过滤临时目录
        if "/tmp" in wd.path or "/var/folders" in wd.path or "/.cache/" in wd.path:
            continue
        # 验证目录是否存在
        if Path(wd.path).exists():
            work_dirs.append(wd.path)

    # 更新缓存
    result = work_dirs[:20]
    _work_dirs_cache = result
    _work_dirs_cache_time = now
    return result


@work_dirs_router.get("/", summary="列出可用工作目录")
async def get_work_dirs() -> list[str]:
    """从元数据中获取可用工作目录列表。

    Returns:
        工作目录路径列表。
    """
    return await asyncio.to_thread(_get_work_dirs_sync)


@work_dirs_router.get("/startup", summary="获取启动目录")
async def get_startup_dir(request: Request) -> str:
    """获取 novel web 启动时的目录。

    Args:
        request: FastAPI 请求对象。

    Returns:
        启动目录路径。
    """
    return request.app.state.startup_dir


@router.get("/{session_id}/git-diff", summary="获取 git 差异统计")
async def get_session_git_diff(session_id: UUID) -> GitDiffStats:
    """获取会话工作目录的 git 差异统计。

    Args:
        session_id: 会话 ID。

    Returns:
        git 差异统计信息。

    Raises:
        HTTPException: 如果会话不存在（404）。
    """
    session = load_session_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    work_dir = Path(str(session.novel_cli_session.work_dir))

    # 检查是否为 git 仓库
    if not (work_dir / ".git").exists():
        return GitDiffStats(is_git_repo=False)

    try:
        files: list[GitFileDiff] = []
        total_add, total_del = 0, 0

        # 检查 HEAD 是否存在（仓库至少有一个提交）
        check_proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--verify",
            "HEAD",
            cwd=str(work_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=get_clean_env(),
        )
        await check_proc.wait()
        has_head = check_proc.returncode == 0

        if has_head:
            # 执行 git diff --numstat HEAD（包括已暂存和未暂存的更改）
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--numstat",
                "HEAD",
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=get_clean_env(),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            # 解析输出
            for line in stdout.decode().strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    add = int(parts[0]) if parts[0] != "-" else 0
                    dele = int(parts[1]) if parts[1] != "-" else 0
                    total_add += add
                    total_del += dele
                    # 确定文件状态
                    file_status: str = "modified"
                    if dele == 0 and add > 0:
                        file_status = "added"
                    elif add == 0 and dele > 0:
                        file_status = "deleted"
                    files.append(
                        GitFileDiff(
                            path=parts[2],
                            additions=add,
                            deletions=dele,
                            status=file_status,  # type: ignore[arg-type]
                        )
                    )

        # 同时获取未跟踪的文件（尚未添加到 git 的新文件）
        untracked_proc = await asyncio.create_subprocess_exec(
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=get_clean_env(),
        )
        untracked_stdout, _ = await asyncio.wait_for(untracked_proc.communicate(), timeout=5.0)

        # 将未跟踪文件添加到结果中
        for line in untracked_stdout.decode().strip().split("\n"):
            if line:
                files.append(
                    GitFileDiff(
                        path=line,
                        additions=0,  # 无法计算未跟踪文件的行数
                        deletions=0,
                        status="added",
                    )
                )

        if not has_head:
            return GitDiffStats(
                is_git_repo=True,
                has_changes=len(files) > 0,
                total_additions=0,
                total_deletions=0,
                files=files,
            )

        return GitDiffStats(
            is_git_repo=True,
            has_changes=len(files) > 0,
            total_additions=total_add,
            total_deletions=total_del,
            files=files,
        )
    except TimeoutError:
        return GitDiffStats(is_git_repo=True, error="Git command timed out")
    except Exception as e:
        return GitDiffStats(is_git_repo=True, error=str(e))


# ─── 默认参数和书籍列表端点 ────────────────────────────────────


class BookInfo(BaseModel):
    """书籍摘要信息。

    Attributes:
        name: 书名。
        chapter_count: 章节数。
    """

    name: str
    chapter_count: int


@defaults_router.get("/defaults", summary="获取 CLI 启动默认参数")
async def get_defaults(request: Request) -> dict[str, str | None]:
    """返回 novel-cli web 启动时通过 CLI 参数指定的默认值。

    Args:
        request: FastAPI 请求对象。

    Returns:
        WebDefaults JSON，包含 agent_file、book_name、work_dir。
    """
    from novel_cli.web.defaults import WebDefaults

    defaults: WebDefaults = request.app.state.defaults
    return defaults.model_dump()


@books_router.get("/books", summary="列出可用书籍")
async def list_books(request: Request) -> list[BookInfo]:
    """从知识库查询所有可用书名及章节数。

    数据库未配置时返回空列表。

    Args:
        request: FastAPI 请求对象。

    Returns:
        书籍列表，每项含 name 和 chapter_count。
    """
    from novel_cli.config import load_config
    from novel_cli.store import NovelStore

    config = load_config()
    novel_db = config.services.novel_db

    # 数据库未配置（无密码）时返回空列表
    if not novel_db.pg_password.get_secret_value():
        return []

    store = NovelStore(novel_db)
    try:
        await store.connect()
        items = await store.list_books_with_counts()
    finally:
        await store.close()

    return [BookInfo(name=name, chapter_count=count) for name, count in items]
