"""Novel CLI web 界面的会话进程管理模块。

本模块负责管理单个会话的 NovelCLI 子进程，包括：
- 启动/停止子进程
- 从 stdout 读取 wire 消息（来自 NovelCLI）
- 向 stdin 写入用户输入（发送到 NovelCLI）
- 将消息广播到已连接的 WebSocket

并发模型：
- `SessionProcess` 是 `session_id` 的长期容器，可能比 worker 重启更长寿命。
- 存活与忙碌状态分离：
  - `is_alive` / `is_running`: worker 子进程存在且未退出。
  - `is_busy`: 至少有一个进行中的 prompt ID。
- WebSocket 广播支持"运行中加入"：
  - 新客户端先回放 `wire.jsonl` 历史。
  - 回放期间的实时消息按 WebSocket 缓冲，之后刷新。

锁：
- `_lock` 保护 worker 生命周期和忙碌状态。
- `_ws_lock` 保护 WebSocket 状态。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import mimetypes
import sys
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from kosong.message import ContentPart, ImageURLPart, TextPart
from PIL import Image
from PIL.Image import Image as PILImage
from pydantic import TypeAdapter
from starlette.websockets import WebSocket, WebSocketState

from novel_cli import logger
from novel_cli.config import load_config
from novel_cli.llm import ModelCapability
from novel_cli.utils.subprocess_env import get_clean_env
from novel_cli.web.models import (
    SessionNoticeEvent,
    SessionNoticePayload,
    SessionState,
    SessionStatus,
)
from novel_cli.web.runner.messages import new_session_status_message
from novel_cli.web.store.sessions import load_session_by_id
from novel_cli.wire.jsonrpc import (
    JSONRPCCancelMessage,
    JSONRPCErrorObject,
    JSONRPCErrorResponse,
    JSONRPCEventMessage,
    JSONRPCInMessage,
    JSONRPCInMessageAdapter,
    JSONRPCOutMessage,
    JSONRPCPromptMessage,
    JSONRPCRequestMessage,
    JSONRPCSuccessResponse,
)
from novel_cli.wire.serde import deserialize_wire_message

JSONRPCOutMessageAdapter = TypeAdapter[JSONRPCOutMessage](JSONRPCOutMessage)


class SessionProcess:
    """管理单个会话的 NovelCLI 子进程。

    负责：
    - 启动/停止子进程
    - 从 stdout 读取（来自 NovelCLI 的 wire 消息）
    - 向 stdin 写入（用户输入发送到 NovelCLI）
    - 将消息广播到已连接的 WebSocket

    Attributes:
        session_id: 会话 ID。
    """

    def __init__(self, session_id: UUID) -> None:
        """初始化会话进程。

        Args:
            session_id: 会话 ID。
        """
        self.session_id = session_id
        self._in_flight_prompt_ids: set[str] = set()
        self._status_seq = 0
        self._worker_id: str | None = None
        self._status = SessionStatus(
            session_id=self.session_id,
            state="stopped",
            seq=self._status_seq,
            worker_id=self._worker_id,
            reason=None,
            detail=None,
            updated_at=datetime.now(UTC),
        )
        self._process: asyncio.subprocess.Process | None = None
        self._websockets: set[WebSocket] = set()
        self._websocket_count = 0
        self._replay_buffers: dict[WebSocket, list[str]] = {}
        self._read_task: asyncio.Task[None] | None = None
        self._expecting_exit = False
        self._lock = asyncio.Lock()
        self._ws_lock = asyncio.Lock()
        self._sent_files: set[str] = set()

    @property
    def is_alive(self) -> bool:
        """worker 子进程是否存在且未退出。"""
        process = self._process
        return process is not None and process.returncode is None

    @property
    def is_running(self) -> bool:
        """向后兼容的名称：表示 worker 存活状态。"""
        return self.is_alive

    @property
    def is_busy(self) -> bool:
        """会话当前是否正在处理 prompt。"""
        return len(self._in_flight_prompt_ids) > 0

    @property
    def status(self) -> SessionStatus:
        """当前运行状态快照。"""
        return self._status

    @property
    def websocket_count(self) -> int:
        """获取已连接 WebSocket 的数量。"""
        return self._websocket_count

    async def send_status_snapshot(self, ws: WebSocket) -> None:
        """将当前状态快照发送到指定 WebSocket。

        Args:
            ws: WebSocket 连接对象。
        """
        await ws.send_text(new_session_status_message(self._status).model_dump_json())

    def _build_status(
        self,
        state: SessionState,
        reason: str | None,
        detail: str | None,
    ) -> SessionStatus | None:
        """如果与当前状态不同，构建新的状态对象。

        Args:
            state: 会话状态。
            reason: 状态变更原因。
            detail: 详细信息。

        Returns:
            新的状态对象，如果与当前相同则返回 None。
        """
        current = self._status
        if (
            current.state == state
            and current.reason == reason
            and current.detail == detail
            and current.worker_id == self._worker_id
        ):
            return None
        self._status_seq += 1
        status = SessionStatus(
            session_id=self.session_id,
            state=state,
            seq=self._status_seq,
            worker_id=self._worker_id,
            reason=reason,
            detail=detail,
            updated_at=datetime.now(UTC),
        )
        self._status = status
        return status

    async def _emit_status(
        self,
        state: SessionState,
        *,
        reason: str | None = None,
        detail: str | None = None,
    ) -> None:
        """如果与当前状态不同，发出状态更新。

        Args:
            state: 会话状态。
            reason: 状态变更原因，可选。
            detail: 详细信息，可选。
        """
        status = self._build_status(state, reason, detail)
        if status is None:
            return
        await self._broadcast(new_session_status_message(status).model_dump_json())

    async def start(
        self,
        *,
        reason: str | None = None,
        detail: str | None = None,
        restart_started_at: float | None = None,
    ) -> None:
        """启动 NovelCLI 子进程。

        Args:
            reason: 启动原因，可选。
            detail: 详细信息，可选。
            restart_started_at: 重启开始时间戳，可选。
        """
        async with self._lock:
            if self.is_alive:
                if self._read_task is None or self._read_task.done():
                    self._read_task = asyncio.create_task(self._read_loop())
                return

            self._in_flight_prompt_ids.clear()
            self._expecting_exit = False
            self._worker_id = str(uuid4())

            # 16MB 缓冲区用于大消息（如 base64 编码的图像）
            STREAM_LIMIT = 16 * 1024 * 1024

            if getattr(sys, "frozen", False):
                worker_cmd = [sys.executable, "__web-worker", str(self.session_id)]
            else:
                worker_cmd = [
                    sys.executable,
                    "-m",
                    "novel_cli.web.runner.worker",
                    str(self.session_id),
                ]

            self._process = await asyncio.create_subprocess_exec(
                *worker_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=STREAM_LIMIT,
                env=get_clean_env(),
            )

            self._read_task = asyncio.create_task(self._read_loop())
            if restart_started_at is not None:
                elapsed_ms = int((time.perf_counter() - restart_started_at) * 1000)
                detail = f"restart_ms={elapsed_ms}"
                await self._emit_status("idle", reason=reason or "start", detail=detail)
                await self._emit_restart_notice(reason=reason, restart_ms=elapsed_ms)
            else:
                await self._emit_status("idle", reason=reason or "start", detail=None)

    async def stop(self) -> None:
        """停止会话：终止 worker 并关闭所有 WebSocket。"""
        await self.stop_worker(reason="stop")
        await self._close_all_websockets()

    async def stop_worker(
        self,
        *,
        reason: str | None = None,
        emit_status: bool = True,
    ) -> None:
        """仅停止 worker 子进程，保持 WebSocket 连接。

        Args:
            reason: 停止原因，可选。
            emit_status: 是否发出状态更新，默认 True。
        """
        async with self._lock:
            self._expecting_exit = True
            if self._process is not None:
                if self._process.returncode is None:
                    self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=10.0)
                except TimeoutError:
                    self._process.kill()
                    await self._process.wait()
                self._process = None

            if self._read_task is not None:
                self._read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._read_task
                self._read_task = None

            self._in_flight_prompt_ids.clear()
            self._worker_id = None
            self._expecting_exit = False
            if emit_status:
                await self._emit_status("stopped", reason=reason or "stop")

    async def restart_worker(self, *, reason: str | None = None) -> None:
        """重启 worker 子进程，不断开 WebSocket。

        Args:
            reason: 重启原因，可选。
        """
        started_at = time.perf_counter()
        await self._emit_status("restarting", reason=reason or "restart")
        await self.stop_worker(reason="restart", emit_status=False)
        await self.start(reason=reason or "restart", restart_started_at=started_at)

    async def _emit_restart_notice(self, *, reason: str | None, restart_ms: int) -> None:
        """向所有 WebSocket 发出重启通知。

        Args:
            reason: 重启原因，可选。
            restart_ms: 重启耗时（毫秒）。
        """
        label = "Session restarted"
        if reason == "config_update":
            label = "Session restarted due to config update"
        payload = SessionNoticePayload(
            text=f"{label} · {restart_ms}ms",
            kind="restart",
            reason=reason,
            restart_ms=restart_ms,
        )
        event = SessionNoticeEvent(payload=payload)
        await self._broadcast(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": event.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        )

    async def _read_loop(self) -> None:
        """从子进程 stdout 读取消息并广播到 WebSocket。"""
        assert self._process is not None
        assert self._process.stdout is not None
        assert self._process.stderr is not None

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    if self._process.stdout.at_eof():
                        if self._expecting_exit:
                            break
                        stderr = await self._process.stderr.read()
                        if not stderr:
                            stderr = b"No stderr"
                        await self._broadcast(
                            JSONRPCErrorResponse(
                                id=str(uuid4()),
                                error=JSONRPCErrorObject(
                                    code=self._process.returncode or -1,
                                    message=stderr.decode("utf-8"),
                                ),
                            ).model_dump_json()
                        )
                        logger.warning(
                            f"Process exited with {self._process.returncode}: "
                            f"{stderr.decode('utf-8')}"
                        )
                        self._in_flight_prompt_ids.clear()
                        await self._emit_status(
                            "error",
                            reason="process_exit",
                            detail=stderr.decode("utf-8"),
                        )
                        break
                    else:
                        continue

                await self._broadcast(line.decode("utf-8").rstrip("\n"))

                # 处理输出消息
                try:
                    msg = json.loads(line)
                    match msg.get("method"):
                        case "event":
                            msg["params"] = deserialize_wire_message(msg["params"])
                            await self._handle_out_message(JSONRPCEventMessage.model_validate(msg))
                        case "request":
                            msg["params"] = deserialize_wire_message(msg["params"])
                            await self._handle_out_message(
                                JSONRPCRequestMessage.model_validate(msg)
                            )
                        case _:
                            if msg.get("error"):
                                await self._handle_out_message(
                                    JSONRPCErrorResponse.model_validate(msg)
                                )
                            else:
                                await self._handle_out_message(
                                    JSONRPCSuccessResponse.model_validate(msg)
                                )
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSONRPC out message: {line}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Unexpected error in read loop: {e.__class__.__name__} {e}")

    async def _handle_out_message(self, message: JSONRPCOutMessage) -> None:
        """处理来自 worker 的输出消息。

        Args:
            message: JSONRPC 输出消息。
        """
        match message:
            case JSONRPCSuccessResponse():
                was_busy = self.is_busy
                if message.id in self._in_flight_prompt_ids:
                    self._in_flight_prompt_ids.remove(message.id)
                if was_busy and not self.is_busy:
                    await self._emit_status("idle", reason="prompt_complete")
            case JSONRPCErrorResponse():
                was_busy = self.is_busy
                if message.id in self._in_flight_prompt_ids:
                    self._in_flight_prompt_ids.remove(message.id)
                if was_busy and not self.is_busy:
                    await self._emit_status("idle", reason="prompt_error")
            case _:
                return

    async def _encode_uploaded_files(self) -> AsyncGenerator[ContentPart]:
        """编码上传的文件以便发送到模型。

        Yields:
            内容部分（文本、图像或视频）。
        """
        session = load_session_by_id(self.session_id)
        assert session is not None

        uploads_dir = session.novel_cli_session.dir / "uploads"
        if not uploads_dir.exists():
            return

        # 加载 fork 留下的 .sent 标记，避免重复发送继承的文件。
        # 标记保留（不删除），以便在进程重启后仍有效。
        sent_marker = uploads_dir / ".sent"
        if sent_marker.exists():
            try:
                already_sent = json.loads(sent_marker.read_text(encoding="utf-8"))
                self._sent_files.update(already_sent)
            except Exception:
                pass

        all_files = sorted(
            (f for f in uploads_dir.iterdir() if f.name != ".sent"),
            key=lambda x: x.name,
        )
        files = [f for f in all_files if f.name not in self._sent_files]

        if not files:
            return

        # 构建文件列表（路径和 MIME 类型）
        file_infos: list[tuple[Path, str]] = []
        for file in files:
            mime_type, _ = mimetypes.guess_type(file.name)
            file_infos.append((file, mime_type or "application/octet-stream"))

        # 输出文件列表摘要
        file_list_lines = ["<uploaded_files>"]
        for idx, (file, _) in enumerate(file_infos, start=1):
            file_list_lines.append(f"{idx}. {file}")
        file_list_lines.append("</uploaded_files>")
        yield TextPart(text="\n".join(file_list_lines) + "\n\n")

        # 文本文件扩展名
        text_extensions = {
            ".txt",
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".xml",
            ".html",
            ".css",
            ".js",
            ".ts",
            ".py",
            ".sh",
            ".csv",
            ".log",
            ".rst",
            ".toml",
            ".ini",
        }

        # 检查模型能力
        config = load_config()
        capabilities: set[ModelCapability] = set()
        if config.default_model:
            capabilities = config.models[config.default_model].capabilities or set()
        is_vision = "image_in" in capabilities
        is_video_in = "video_in" in capabilities

        # 处理每个文件
        for file, mime_type in file_infos:
            file_path = str(file)
            ext = file.suffix.lower()

            if is_vision and mime_type.startswith("image/"):
                try:
                    content = file.read_bytes()
                    with Image.open(io.BytesIO(content)) as img:
                        pil_img: PILImage = img
                        width, height = pil_img.size
                        max_side = max(width, height)
                        if max_side > 4096:
                            scale = 4096 / max_side
                            new_size = (int(width * scale), int(height * scale))
                            pil_img = pil_img.resize(  # pyright: ignore[reportUnknownMemberType]
                                new_size
                            )
                        buffer = io.BytesIO()
                        pil_img.save(buffer, format="PNG")
                        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                        tag = f'<image path="{file_path}" content_type="{mime_type}">'
                        yield TextPart(text=tag)
                        yield ImageURLPart(
                            image_url=ImageURLPart.ImageURL(url=f"data:image/png;base64,{encoded}")
                        )
                        yield TextPart(text="</image>\n\n")
                except Exception:
                    # 跳过编码失败的文件——不要阻塞上传
                    pass
            elif is_video_in and mime_type.startswith("video/"):
                # 对于视频文件，发出 <video> 标签供前端显示，但不嵌入内容。
                # agent 将使用 ReadMediaFile 工具读取它，该工具正确处理视频上传。
                yield TextPart(text=f'<video path="{file_path}" content_type="{mime_type}">')
                yield TextPart(text="</video>\n\n")
            elif ext in text_extensions or mime_type.startswith("text/"):
                try:
                    content = file.read_bytes()
                    text_content = content.decode("utf-8", errors="replace")
                    yield TextPart(text=f'<document path="{file_path}" content_type="{mime_type}">')
                    yield TextPart(text=text_content)
                    yield TextPart(text="</document>\n\n")
                except Exception:
                    # 跳过解码失败的文件——不要阻塞上传
                    pass

        # 标记文件为已发送
        for file in files:
            self._sent_files.add(file.name)

    async def _handle_in_message(self, message: JSONRPCInMessage) -> str | None:
        """处理发往 worker 的输入消息，编码上传的文件。

        Args:
            message: JSONRPC 输入消息。

        Returns:
            处理后的消息 JSON 字符串，如果不需要处理则返回 None。
        """
        match message:
            case JSONRPCPromptMessage():
                user_input: list[ContentPart] = []
                async for part in self._encode_uploaded_files():
                    user_input.append(part)
                # 仅上传文件的特殊标记
                if isinstance(message.params.user_input, str):
                    if message.params.user_input != "NOVEL_FILE_UPLOAD_WITHOUT_MESSAGE":
                        user_input.append(TextPart(text=message.params.user_input))
                else:
                    user_input += message.params.user_input
                return json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "prompt",
                        "id": message.id,
                        "params": {
                            "user_input": [part.model_dump(mode="json") for part in user_input],
                        },
                    },
                    ensure_ascii=False,
                )
            case _:
                return None
        return None

    async def _broadcast(self, message: str) -> None:
        """将消息广播到所有已连接的 WebSocket。

        Args:
            message: 要广播的消息字符串。
        """
        disconnected: set[WebSocket] = set()

        async with self._ws_lock:
            websockets = list(self._websockets)
            to_send: list[WebSocket] = []
            for ws in websockets:
                buffer = self._replay_buffers.get(ws)
                if buffer is not None:
                    buffer.append(message)
                else:
                    to_send.append(ws)

        for ws in to_send:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(message)
                else:
                    disconnected.add(ws)
            except Exception as e:
                logger.warning(f"websocket failed: {e.__class__.__name__} {e}")
                disconnected.add(ws)

        if disconnected:
            async with self._ws_lock:
                self._websockets -= disconnected
                self._websocket_count = len(self._websockets)
                for ws in disconnected:
                    self._replay_buffers.pop(ws, None)
            logger.debug(
                f"Broadcast: removed {len(disconnected)} disconnected ws, "
                f"remaining={self._websocket_count}"
            )

    async def add_websocket_and_begin_replay(self, ws: WebSocket) -> None:
        """原子地附加 WebSocket 并进入回放模式。

        Args:
            ws: WebSocket 连接对象。
        """
        async with self._ws_lock:
            if ws not in self._websockets:
                self._websockets.add(ws)
                self._websocket_count = len(self._websockets)
            self._replay_buffers.setdefault(ws, [])
        logger.debug(f"WebSocket added (replay mode), count={self._websocket_count}")

    async def end_replay(self, ws: WebSocket) -> None:
        """历史回放后刷新 WebSocket 的缓冲实时消息。

        Args:
            ws: WebSocket 连接对象。
        """
        while True:
            async with self._ws_lock:
                buffer = self._replay_buffers.get(ws)
                if buffer is None:
                    return
                if not buffer:
                    self._replay_buffers.pop(ws, None)
                    return
                chunk = buffer.copy()
                buffer.clear()

            if ws.client_state != WebSocketState.CONNECTED:
                logger.warning("end_replay: ws not connected, cleaning up replay buffer")
                async with self._ws_lock:
                    self._replay_buffers.pop(ws, None)
                return
            for message in chunk:
                try:
                    await ws.send_text(message)
                except Exception as e:
                    # 发送失败——弹出回放缓冲区，以便下次调用时
                    # _broadcast() 直接发送（或检测断开）。
                    # 不要在此处从 _websockets 移除 ws；让 _broadcast()
                    # 或 session_stream 的 finally 块处理清理。
                    logger.warning(f"end_replay: send_text failed during buffer flush: {e}")
                    async with self._ws_lock:
                        self._replay_buffers.pop(ws, None)
                    return

    async def _close_all_websockets(self) -> None:
        """关闭所有已连接的 WebSocket。"""
        async with self._ws_lock:
            websockets = list(self._websockets)
            self._websockets.clear()
            self._websocket_count = 0
            self._replay_buffers.clear()

        for ws in websockets:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.close(code=1001, reason="Session process exited")
            except Exception:
                # 忽略关闭已断开 WebSocket 时的错误
                pass

    async def remove_websocket(self, ws: WebSocket) -> None:
        """从会话中移除 WebSocket 连接。

        Args:
            ws: WebSocket 连接对象。
        """
        async with self._ws_lock:
            if ws in self._websockets:
                self._websockets.discard(ws)
                self._websocket_count = len(self._websockets)
                logger.debug(f"WebSocket removed, count={self._websocket_count}")
            self._replay_buffers.pop(ws, None)

    async def send_message(self, message: str) -> None:
        """发送消息到子进程 stdin。

        Args:
            message: 要发送的消息字符串。
        """
        await self.start()
        process = self._process
        assert process is not None
        assert process.stdin is not None

        # 处理输入消息
        try:
            in_message = JSONRPCInMessageAdapter.validate_json(message)
            if isinstance(in_message, JSONRPCPromptMessage):
                was_busy = self.is_busy
                self._in_flight_prompt_ids.add(in_message.id)
                if not was_busy:
                    await self._emit_status("busy", reason="prompt")
            elif isinstance(in_message, JSONRPCCancelMessage) and not self.is_busy:
                # 如果不忙碌，返回成功以避免错误
                await self._broadcast(
                    JSONRPCSuccessResponse(id=in_message.id, result={}).model_dump_json()
                )
                return

            new_message = await self._handle_in_message(in_message)
            if new_message is not None:
                message = new_message
        except ValueError as e:
            logger.error(f"{e.__class__.__name__} {e}: Invalid JSONRPC in message: {message}")
            return

        process.stdin.write((message + "\n").encode("utf-8"))
        await process.stdin.drain()


class NovelCLIRunner:
    """管理多个会话进程。

    Attributes:
        _sessions: 会话进程字典，键为会话 ID。
    """

    def __init__(self) -> None:
        """初始化运行器。"""
        self._sessions: dict[UUID, SessionProcess] = {}
        self._lock = asyncio.Lock()

    def start(self) -> None:
        """启动运行器（空操作，会话按需启动）。"""
        pass

    async def stop(self) -> None:
        """停止所有运行中的会话。"""
        tasks: list[asyncio.Task[None]] = []
        for session in self._sessions.values():
            if session.is_running:
                tasks.append(asyncio.create_task(session.stop()))
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=5.0)
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

    async def get_or_create_session(self, session_id: UUID) -> SessionProcess:
        """获取或创建会话进程。

        Args:
            session_id: 会话 ID。

        Returns:
            会话进程实例。
        """
        async with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionProcess(session_id)
            return self._sessions[session_id]

    def get_session(self, session_id: UUID) -> SessionProcess | None:
        """获取会话进程（如果存在）。

        Args:
            session_id: 会话 ID。

        Returns:
            会话进程实例，如果不存在则返回 None。
        """
        return self._sessions.get(session_id)

    async def detach_websocket(self, ws: WebSocket, session_id: UUID) -> None:
        """从会话中分离 WebSocket。

        Args:
            ws: WebSocket 连接对象。
            session_id: 会话 ID。
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session:
                await session.remove_websocket(ws)

    async def restart_running_workers(
        self,
        *,
        reason: str,
        force: bool,
    ) -> RestartWorkersSummary:
        """重启所有运行中的 worker 以应用全局配置更新。

        Args:
            reason: 重启原因（如 "config_update"）。
            force: 如果为 True，也重启忙碌的会话（可能打断 prompt）。

        Returns:
            重启和跳过的会话摘要。
        """
        async with self._lock:
            running = [(sid, proc) for sid, proc in self._sessions.items() if proc.is_running]

        restarted: list[UUID] = []
        skipped_busy: list[UUID] = []
        tasks: list[asyncio.Task[None]] = []

        for session_id, proc in running:
            if proc.is_busy and not force:
                skipped_busy.append(session_id)
                continue
            restarted.append(session_id)
            tasks.append(asyncio.create_task(proc.restart_worker(reason=reason)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return RestartWorkersSummary(
            restarted_session_ids=restarted,
            skipped_busy_session_ids=skipped_busy,
        )


@dataclass(slots=True)
class RestartWorkersSummary:
    """restart_running_workers 操作的摘要。

    Attributes:
        restarted_session_ids: 已重启的会话 ID 列表。
        skipped_busy_session_ids: 因忙碌而跳过的会话 ID 列表。
    """

    restarted_session_ids: list[UUID]
    skipped_busy_session_ids: list[UUID]
