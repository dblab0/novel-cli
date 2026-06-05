"""后台任务数据模型模块。

定义任务规格、运行状态、控制信息和输出块等核心数据结构。
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 任务类型：Bash 命令或 Agent
type TaskKind = Literal["bash", "agent"]

# 任务状态枚举
type TaskStatus = Literal[
    "created",
    "starting",
    "running",
    "awaiting_approval",
    "completed",
    "failed",
    "killed",
    "lost",
]

# 任务所属角色
type TaskOwnerRole = Literal["root", "subagent"]

# 终态状态集合：任务进入这些状态后不再变化
TERMINAL_TASK_STATUSES: tuple[TaskStatus, ...] = ("completed", "failed", "killed", "lost")


def is_terminal_status(status: TaskStatus) -> bool:
    """判断任务状态是否为终态。

    Args:
        status: 任务状态。

    Returns:
        如果状态为终态则返回 True，否则返回 False。
    """
    return status in TERMINAL_TASK_STATUSES


class TaskSpec(BaseModel):
    """任务规格数据模型。

    定义任务的静态配置信息，包括任务类型、描述、命令等。

    Attributes:
        version: 数据版本号。
        id: 任务唯一标识符。
        kind: 任务类型（bash 或 agent）。
        session_id: 所属会话 ID。
        description: 任务描述。
        tool_call_id: 调用此任务的 tool_call ID。
        owner_role: 任务所属角色（root 或 subagent）。
        created_at: 任务创建时间戳。
        command: Bash 任务的命令内容。
        shell_name: Shell 名称。
        shell_path: Shell 可执行文件路径。
        cwd: 命令执行的工作目录。
        timeout_s: 任务超时时间（秒）。
        kind_payload: 扩展 payload，用于未来任务类型。
    """

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    id: str
    kind: TaskKind
    session_id: str
    description: str
    tool_call_id: str
    owner_role: TaskOwnerRole = "root"
    created_at: float = Field(default_factory=time.time)

    @field_validator("owner_role", mode="before")
    @classmethod
    def _normalize_owner_role(cls, v: str) -> str:
        """规范化 owner_role 字段。

        将旧版 role 值（fixed_subagent, dynamic_subagent）统一映射为 subagent。

        Args:
            v: 原始 role 值。

        Returns:
            规范化后的 role 值。
        """
        if v in ("fixed_subagent", "dynamic_subagent"):
            return "subagent"
        return v

    # Bash 专用字段（V1 版本）。未来任务类型可使用 kind_payload。
    command: str | None = None
    shell_name: str | None = None
    shell_path: str | None = None
    cwd: str | None = None
    timeout_s: int | None = None
    kind_payload: dict[str, Any] | None = None


class TaskRuntime(BaseModel):
    """任务运行状态数据模型。

    记录任务的实时执行状态，包括进程信息、时间戳等。

    Attributes:
        status: 当前任务状态。
        worker_pid: Worker 进程 PID。
        child_pid: 子进程 PID。
        child_pgid: 子进程进程组 ID。
        started_at: 任务启动时间戳。
        heartbeat_at: 最近心跳时间戳。
        updated_at: 最近更新时间戳。
        finished_at: 任务结束时间戳。
        exit_code: 任务退出码。
        interrupted: 是否被中断。
        timed_out: 是否超时。
        failure_reason: 失败原因描述。
    """

    model_config = ConfigDict(extra="ignore")

    status: TaskStatus = "created"
    worker_pid: int | None = None
    child_pid: int | None = None
    child_pgid: int | None = None
    started_at: float | None = None
    heartbeat_at: float | None = None
    updated_at: float = Field(default_factory=time.time)
    finished_at: float | None = None
    exit_code: int | None = None
    interrupted: bool = False
    timed_out: bool = False
    failure_reason: str | None = None


class TaskControl(BaseModel):
    """任务控制数据模型。

    记录对任务的控制指令，如终止请求等。

    Attributes:
        kill_requested_at: 终止请求时间戳。
        kill_reason: 终止原因描述。
        force: 是否强制终止。
    """

    model_config = ConfigDict(extra="ignore")

    kill_requested_at: float | None = None
    kill_reason: str | None = None
    force: bool = False


class TaskConsumerState(BaseModel):
    """任务消费者状态数据模型。

    记录任务输出消费进度，用于增量读取。

    Attributes:
        last_seen_output_size: 最近查看的输出大小（字节）。
        last_viewed_at: 最近查看时间戳。
    """

    model_config = ConfigDict(extra="ignore")

    last_seen_output_size: int = 0
    last_viewed_at: float | None = None


class TaskView(BaseModel):
    """任务视图数据模型。

    聚合任务的规格、运行状态、控制信息和消费者状态。

    Attributes:
        spec: 任务规格。
        runtime: 运行状态。
        control: 控制信息。
        consumer: 消费者状态。
    """

    model_config = ConfigDict(extra="ignore")

    spec: TaskSpec
    runtime: TaskRuntime
    control: TaskControl
    consumer: TaskConsumerState


class TaskOutputChunk(BaseModel):
    """任务输出块数据模型。

    表示任务输出的一次读取结果。

    Attributes:
        task_id: 任务 ID。
        offset: 本次读取起始偏移量。
        next_offset: 下次读取起始偏移量。
        text: 输出文本内容。
        eof: 是否已读到文件末尾。
        status: 读取时的任务状态。
    """

    model_config = ConfigDict(extra="ignore")

    task_id: str
    offset: int
    next_offset: int
    text: str
    eof: bool
    status: TaskStatus
