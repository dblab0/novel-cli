"""Novel CLI Web UI 数据模型。

定义 Web 界面使用的会话状态、Git 差异统计等数据结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SessionState = Literal["stopped", "idle", "busy", "restarting", "error"]


class SessionStatus(BaseModel):
    """Web 会话运行状态。

    Attributes:
        session_id: 会话唯一 ID。
        state: 当前会话状态。
        seq: 单调递增的序列号。
        worker_id: 工作进程实例 ID。
        reason: 状态转换的原因。
        detail: 用于调试的额外详情。
        updated_at: 状态更新的时间戳。
    """

    session_id: UUID = Field(..., description="Session unique ID")
    state: SessionState = Field(..., description="Current session state")
    seq: int = Field(..., description="Monotonic sequence number")
    worker_id: str | None = Field(default=None, description="Worker instance ID")
    reason: str | None = Field(default=None, description="Reason for the state transition")
    detail: str | None = Field(default=None, description="Additional detail for debugging")
    updated_at: datetime = Field(..., description="Timestamp for this state")


class SessionNoticePayload(BaseModel):
    """会话通知事件的内容。

    Attributes:
        text: 通知显示文本。
        kind: 通知类型。
        reason: 通知原因。
        restart_ms: 重启等待时间（毫秒）。
    """

    text: str = Field(..., description="Display text for the notice")
    kind: Literal["restart"] = Field(default="restart", description="Notice type")
    reason: str | None = Field(default=None, description="Reason for the notice")
    restart_ms: int | None = Field(default=None, description="Restart duration in ms")


class SessionNoticeEvent(BaseModel):
    """发送到前端的会话通知事件。

    Attributes:
        type: 事件类型。
        payload: 通知内容。
    """

    type: Literal["SessionNotice"] = Field(default="SessionNotice", description="Event type")
    payload: SessionNoticePayload


class GitFileDiff(BaseModel):
    """单个文件的 Git 差异统计。

    Attributes:
        path: 文件路径。
        additions: 新增行数。
        deletions: 删除行数。
        status: 文件变更状态。
    """

    path: str = Field(..., description="File path")
    additions: int = Field(..., description="Number of added lines")
    deletions: int = Field(..., description="Number of deleted lines")
    status: Literal["added", "modified", "deleted", "renamed"] = Field(
        ..., description="File change status"
    )


class GitDiffStats(BaseModel):
    """工作目录的 Git 差异统计。

    Attributes:
        is_git_repo: 目录是否为 Git 仓库。
        has_changes: 是否有未提交的变更。
        total_additions: 总新增行数。
        total_deletions: 总删除行数。
        files: 各文件的差异统计列表。
        error: 错误信息（如有）。
    """

    is_git_repo: bool = Field(..., description="Whether the directory is a git repo")
    has_changes: bool = Field(default=False, description="Whether there are uncommitted changes")
    total_additions: int = Field(default=0, description="Total added lines")
    total_deletions: int = Field(default=0, description="Total deleted lines")
    files: list[GitFileDiff] = Field(default=[], description="Per-file diff stats")
    error: str | None = Field(default=None, description="Error message if any")


class Session(BaseModel):
    """Web UI 会话元数据。

    Attributes:
        session_id: 会话唯一 ID。
        title: 从 novel-cli 历史记录提取的会话标题。
        last_updated: 最后更新时间戳。
        is_running: 会话是否正在运行。
        status: 会话运行状态。
        work_dir: 会话的工作目录。
        session_dir: 会话目录路径。
        archived: 会话是否已归档。
    """

    session_id: UUID = Field(..., description="Session unique ID")
    title: str = Field(..., description="Session title derived from novel-cli history")
    last_updated: datetime = Field(..., description="Last updated timestamp")
    is_running: bool = Field(default=False, description="Whether the session is running")
    status: SessionStatus | None = Field(default=None, description="Session runtime status")
    work_dir: str | None = Field(default=None, description="Working directory for the session")
    session_dir: str | None = Field(default=None, description="Session directory path")
    archived: bool = Field(default=False, description="Whether the session is archived")


class UpdateSessionRequest(BaseModel):
    """更新会话请求模型。

    Attributes:
        title: 新标题，长度限制 1-200 字符。
        archived: 归档或取消归档状态。
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = Field(default=None, description="Archive or unarchive the session")


class GenerateTitleRequest(BaseModel):
    """生成标题请求模型。

    参数可选，若未提供，后端将自动从 wire.jsonl 文件读取。

    Attributes:
        user_message: 用户消息内容。
        assistant_response: AI 回复内容。
    """

    user_message: str | None = None
    assistant_response: str | None = None


class GenerateTitleResponse(BaseModel):
    """生成标题响应模型。

    Attributes:
        title: 生成的标题。
    """

    title: str
