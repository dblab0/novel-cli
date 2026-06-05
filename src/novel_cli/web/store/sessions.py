"""Web UI 会话存储模块，提供简单的内存缓存。

设计理念
========

本模块采用简单的旁路缓存模式（cache-aside pattern），配合 TTL 回退机制：

1. **读时缓存**：首次读取时填充缓存，后续读取命中缓存
2. **写时失效**：API 变更操作调用 invalidate_sessions_cache() 使缓存失效
3. **TTL 回退**：缓存会在 CACHE_TTL 秒后过期，作为安全保障机制

适用场景
========

本设计适用于以下场景：
- 单工作进程（例如不带 -w 参数的 `uvicorn app:app`）
- 所有变更操作通过同一 API 进行
- 可接受短时间内的数据过期（最长 CACHE_TTL 秒）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import ConfigDict, Field

from novel_cli.metadata import WorkDirMeta, load_metadata
from novel_cli.session import Session as NovelCLISession
from novel_cli.session_state import SessionState, load_session_state, save_session_state
from novel_cli.web.models import Session
from novel_cli.wire.file import WireFile

# 缓存配置
CACHE_TTL = 5.0  # 秒 - 平衡数据新鲜度与性能

# 自动归档配置
AUTO_ARCHIVE_DAYS = 15  # 超过此天数的会话将被自动归档

_sessions_cache: list[JointSession] | None = None
_cache_timestamp: float = 0.0
_sessions_index_cache: list[SessionIndexEntry] | None = None
_index_cache_timestamp: float = 0.0


def invalidate_sessions_cache() -> None:
    """清除会话缓存。

    在任何变更操作（创建/更新/删除）后调用此函数。
    确保下次读取时能获取最新数据。
    """
    global _sessions_cache, _cache_timestamp, _sessions_index_cache, _index_cache_timestamp
    _sessions_cache = None
    _cache_timestamp = 0.0
    _sessions_index_cache = None
    _index_cache_timestamp = 0.0


class JointSession(Session):
    """联合会话模型，同时包含 Web UI 和 novel-cli 会话数据。

    Attributes:
        novel_cli_session: Novel CLI 会话实例（不包含在序列化输出中）。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    novel_cli_session: NovelCLISession = Field(exclude=True)


@dataclass(slots=True)
class SessionIndexEntry:
    """会话索引条目，用于缓存会话元数据。

    Attributes:
        session_id: 会话唯一标识符。
        session_dir: 会话目录路径。
        context_file: 上下文文件路径。
        work_dir: 工作目录路径。
        work_dir_meta: 工作目录元数据。
        last_updated: 最后更新时间。
        title: 会话标题。
        state: 会话状态。
    """

    session_id: UUID
    session_dir: Path
    context_file: Path
    work_dir: str
    work_dir_meta: WorkDirMeta
    last_updated: datetime
    title: str
    state: SessionState


def _derive_title_from_wire(session_dir: Path) -> str:
    wire_file = session_dir / "wire.jsonl"
    if not wire_file.exists():
        return "Untitled"

    try:
        import json

        from kosong.message import Message

        from novel_cli.utils.string import shorten

        with open(wire_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    message = record.get("message", {})
                    if message.get("type") == "TurnBegin":
                        user_input = message.get("payload", {}).get("user_input")
                        if user_input:
                            msg = Message(role="user", content=user_input)
                            text = msg.extract_text(" ")
                            return shorten(text, width=300)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return "Untitled"


def _iter_session_dirs(wd: WorkDirMeta) -> list[tuple[Path, Path]]:
    """遍历工作目录下的所有会话目录。

    Args:
        wd: 工作目录元数据。

    Returns:
        (session_dir, context_file) 元组列表。
    """
    session_dirs: list[tuple[Path, Path]] = []

    # 最新会话
    for context_file in wd.sessions_dir.glob("*/context.jsonl"):
        session_dir = context_file.parent
        session_dirs.append((session_dir, context_file))

    # 旧版会话
    for context_file in wd.sessions_dir.glob("*.jsonl"):
        session_dir = context_file.parent / context_file.stem
        converted_context_file = session_dir / "context.jsonl"
        if converted_context_file.exists():
            continue
        session_dirs.append((session_dir, context_file))

    return session_dirs


def _ensure_title(entry: SessionIndexEntry, *, refresh: bool) -> None:
    """确保会话具有标题。

    仅从 SessionState.custom_title 读取标题（作为唯一数据源）。
    如果 custom_title 未设置，则从 wire.jsonl 文件推导。

    Args:
        entry: 会话索引条目。
        refresh: 是否强制刷新标题（从 wire.jsonl 重新推导）。
    """
    if entry.state.custom_title:
        entry.title = entry.state.custom_title
        return

    if not refresh:
        if entry.title and entry.title != "Untitled":
            return
        entry.title = "Untitled"
        return

    # 从 wire.jsonl 推导标题（不缓存 — 标题将由 soul 层或 generate-title 端点持久化到 SessionState）
    entry.title = _derive_title_from_wire(entry.session_dir)


def _build_novel_session(entry: SessionIndexEntry) -> NovelCLISession:
    """根据索引条目构建 Novel CLI 会话实例。

    Args:
        entry: 会话索引条目。

    Returns:
        构建的 NovelCLISession 实例。
    """
    from kaos.path import KaosPath

    return NovelCLISession(
        id=str(entry.session_id),
        work_dir=KaosPath.unsafe_from_local_path(Path(entry.work_dir)),
        work_dir_meta=entry.work_dir_meta,
        context_file=entry.context_file,
        wire_file=WireFile(entry.session_dir / "wire.jsonl"),
        state=entry.state,
        title=entry.title,
        updated_at=entry.last_updated.timestamp(),
    )


def _build_joint_session(entry: SessionIndexEntry) -> JointSession:
    """根据索引条目构建联合会话实例。

    Args:
        entry: 会话索引条目。

    Returns:
        构建的 JointSession 实例。
    """
    novel_session = _build_novel_session(entry)
    return JointSession(
        session_id=entry.session_id,
        title=entry.title,
        last_updated=entry.last_updated,
        is_running=False,
        status=None,
        work_dir=entry.work_dir,
        session_dir=str(entry.session_dir),
        novel_cli_session=novel_session,
        archived=entry.state.archived,
    )


def _should_auto_archive(last_updated: datetime, state: SessionState) -> bool:
    """检查会话是否应根据时间和豁免状态自动归档。

    Args:
        last_updated: 会话最后更新时间。
        state: 会话状态。

    Returns:
        如果会话应被自动归档则返回 True，否则返回 False。
    """
    if state.archived:
        return False

    if state.auto_archive_exempt:
        return False

    now = datetime.now(tz=UTC)
    age_days = (now - last_updated).days
    return age_days >= AUTO_ARCHIVE_DAYS


def _build_sessions_index() -> list[SessionIndexEntry]:
    """从磁盘构建会话索引。

    注意：本函数仅读取数据，不执行自动归档写入操作。
    自动归档由 run_auto_archive() 单独处理，以避免在读操作中执行磁盘写入。

    Returns:
        按最后更新时间降序排列的会话索引条目列表。
    """
    metadata = load_metadata()
    entries: list[SessionIndexEntry] = []

    for wd in metadata.work_dirs:
        for session_dir, context_file in _iter_session_dirs(wd):
            try:
                session_id = UUID(session_dir.name)
            except (ValueError, AttributeError, TypeError):
                continue

            if not context_file.exists():
                continue

            last_updated = datetime.fromtimestamp(context_file.stat().st_mtime, tz=UTC)
            state = load_session_state(session_dir)
            title = state.custom_title or "Untitled"

            entries.append(
                SessionIndexEntry(
                    session_id=session_id,
                    session_dir=session_dir,
                    context_file=context_file,
                    work_dir=wd.path,
                    work_dir_meta=wd,
                    last_updated=last_updated,
                    title=title,
                    state=state,
                )
            )

    entries.sort(key=lambda x: (x.last_updated, str(x.session_id)), reverse=True)
    return entries


# 追踪上次自动归档时间，避免过于频繁执行
_last_auto_archive_time: float = 0.0
AUTO_ARCHIVE_INTERVAL = 300.0  # 自动归档最多每 5 分钟执行一次


def run_auto_archive() -> int:
    """对旧会话执行自动归档。

    本函数设计为周期性调用（例如应用启动时，或通过后台任务），
    而非在每次读操作时执行。

    Returns:
        被自动归档的会话数量。
    """
    global _last_auto_archive_time

    now = time.time()
    if now - _last_auto_archive_time < AUTO_ARCHIVE_INTERVAL:
        return 0

    _last_auto_archive_time = now
    archived_count = 0

    # 加载最新索引（绕过缓存以获取当前状态）
    entries = _build_sessions_index()

    for entry in entries:
        if _should_auto_archive(entry.last_updated, entry.state):
            if not entry.session_dir.is_dir():
                continue
            entry.state.archived = True
            entry.state.archived_at = time.time()
            save_session_state(entry.state, entry.session_dir)
            archived_count += 1

    # 如果归档了任何会话，则使缓存失效
    if archived_count > 0:
        invalidate_sessions_cache()

    return archived_count


def _load_sessions_index_cached() -> list[SessionIndexEntry]:
    global _sessions_index_cache, _index_cache_timestamp

    now = time.time()
    if _sessions_index_cache is not None and (now - _index_cache_timestamp) < CACHE_TTL:
        return _sessions_index_cache

    _sessions_index_cache = _build_sessions_index()
    _index_cache_timestamp = now
    return _sessions_index_cache


def load_all_sessions() -> list[JointSession]:
    """从所有工作目录加载所有会话。

    Returns:
        所有联合会话列表，按最后更新时间降序排列。
    """
    entries = _load_sessions_index_cached()
    sessions: list[JointSession] = []

    for entry in entries:
        _ensure_title(entry, refresh=False)
        sessions.append(_build_joint_session(entry))

    sessions.sort(key=lambda x: x.last_updated, reverse=True)
    return sessions


def load_all_sessions_cached() -> list[JointSession]:
    """带缓存的 load_all_sessions() 版本。

    当满足以下条件时返回缓存数据：
    - 缓存存在 且
    - 缓存未超过 CACHE_TTL

    否则，从磁盘刷新数据并更新缓存。

    Returns:
        联合会话列表（可能来自缓存）。
    """
    global _sessions_cache, _cache_timestamp

    now = time.time()
    if _sessions_cache is not None and (now - _cache_timestamp) < CACHE_TTL:
        return _sessions_cache

    _sessions_cache = load_all_sessions()
    _cache_timestamp = now
    return _sessions_cache


def load_sessions_page(
    *,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    archived: bool | None = None,
) -> list[JointSession]:
    """加载分页的会话列表，可按查询条件和归档状态筛选。

    Args:
        limit: 返回的最大会话数量，默认为 100。
        offset: 跳过的会话数量，默认为 0。
        query: 可选的搜索查询，用于按标题或工作目录筛选。
        archived: 按归档状态筛选：
            - None（默认）：仅返回未归档的会话。
            - True：仅返回已归档的会话。
            - False：仅返回未归档的会话。

    Returns:
        筛选后的联合会话列表。
    """
    entries = list(_load_sessions_index_cached())

    # 按归档状态筛选
    if archived is None or archived is False:
        entries = [e for e in entries if not e.state.archived]
    else:
        entries = [e for e in entries if e.state.archived]

    if query:
        query_text = query.strip().lower()
        if query_text:
            for entry in entries:
                _ensure_title(entry, refresh=True)
            entries = [
                entry
                for entry in entries
                if query_text in entry.title.lower() or query_text in (entry.work_dir or "").lower()
            ]

    if offset < 0:
        offset = 0
    if limit <= 0:
        limit = 100

    page_entries = entries[offset : offset + limit]

    if not query:
        for entry in page_entries:
            if not entry.title or entry.title == "Untitled":
                _ensure_title(entry, refresh=True)

    return [_build_joint_session(entry) for entry in page_entries]


def load_session_by_id(id: UUID) -> JointSession | None:
    """按 ID 加载会话。

    本函数首先检查缓存/磁盘扫描，然后如果未找到，
    则直接从元数据构造会话（处理新创建的空上下文文件会话）。

    Args:
        id: 会话的唯一标识符。

    Returns:
        找到的联合会话实例，如果未找到则返回 None。
    """
    global_metadata = load_metadata()
    session_id_str = str(id)

    for wd in global_metadata.work_dirs:
        session_dir = wd.sessions_dir / session_id_str
        context_file = session_dir / "context.jsonl"

        if context_file.exists():
            last_updated = datetime.fromtimestamp(context_file.stat().st_mtime, tz=UTC)
            state = load_session_state(session_dir)
            entry = SessionIndexEntry(
                session_id=id,
                session_dir=session_dir,
                context_file=context_file,
                work_dir=wd.path,
                work_dir_meta=wd,
                last_updated=last_updated,
                title="Untitled",
                state=state,
            )
            _ensure_title(entry, refresh=True)
            return _build_joint_session(entry)

        # 旧版会话：context.jsonl 直接存储在 sessions_dir 中
        legacy_context = wd.sessions_dir / f"{session_id_str}.jsonl"
        if legacy_context.exists():
            last_updated = datetime.fromtimestamp(legacy_context.stat().st_mtime, tz=UTC)
            state = load_session_state(session_dir)
            entry = SessionIndexEntry(
                session_id=id,
                session_dir=session_dir,
                context_file=legacy_context,
                work_dir=wd.path,
                work_dir_meta=wd,
                last_updated=last_updated,
                title="Untitled",
                state=state,
            )
            _ensure_title(entry, refresh=True)
            return _build_joint_session(entry)

    return None


if __name__ == "__main__":
    start_time = time.time()
    sessions = load_all_sessions()
    print(f"Found {len(sessions)} Sessions in {time.time() - start_time:.2f} seconds:")
    for session in sessions:
        print(session.last_updated, session.session_id, session.title)
