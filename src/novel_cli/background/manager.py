"""后台任务管理器模块。

提供后台任务的创建、查询、控制、恢复和通知发布等核心管理功能。
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kaos.local import local_kaos

from novel_cli.config import BackgroundConfig
from novel_cli.notifications import NotificationEvent, NotificationManager
from novel_cli.session import Session
from novel_cli.utils.logging import logger

if TYPE_CHECKING:
    from novel_cli.soul.agent import Runtime

from .ids import generate_task_id
from .models import (
    TaskOutputChunk,
    TaskRuntime,
    TaskSpec,
    TaskStatus,
    TaskView,
    is_terminal_status,
)
from .store import BackgroundTaskStore


class BackgroundTaskManager:
    """后台任务管理器。

    管理后台任务的生命周期，包括创建、执行、监控、终止和通知。

    Attributes:
        completion_event: 任务终态通知事件。
        store: 任务存储管理器。
        role: 管理器所属角色。
    """

    def __init__(
        self,
        session: Session,
        config: BackgroundConfig,
        *,
        notifications: NotificationManager,
        owner_role: str = "root",
    ) -> None:
        """初始化后台任务管理器。

        Args:
            session: 会话实例。
            config: 后台任务配置。
            notifications: 通知管理器。
            owner_role: 所属角色（默认为 root）。
        """
        self._session = session
        self._config = config
        self._notifications = notifications
        self._owner_role = owner_role
        self._store = BackgroundTaskStore(session.context_file.parent / "tasks")
        self._runtime: Runtime | None = None
        self._live_agent_tasks: dict[str, asyncio.Task[None]] = {}
        self._completion_event: asyncio.Event = asyncio.Event()

    @property
    def completion_event(self) -> asyncio.Event:
        """任务终态通知事件。

        当有新的终态通知发布时触发。通知并非在任务进入终态时立即发布，
        而是在 ``reconcile()`` / ``publish_terminal_notifications()`` 执行后。
        重复的通知不会再次触发信号。

        Returns:
            asyncio.Event 事件对象。
        """
        return self._completion_event

    @property
    def store(self) -> BackgroundTaskStore:
        """任务存储管理器。

        Returns:
            BackgroundTaskStore 实例。
        """
        return self._store

    @property
    def role(self) -> str:
        """管理器所属角色。

        Returns:
            角色标识字符串。
        """
        return self._owner_role

    def copy_for_role(self, role: str) -> BackgroundTaskManager:
        """为指定角色创建管理器副本。

        Args:
            role: 目标角色标识。

        Returns:
            新的 BackgroundTaskManager 实例。
        """
        manager = BackgroundTaskManager(
            self._session,
            self._config,
            notifications=self._notifications,
            owner_role=role,
        )
        manager._runtime = self._runtime
        return manager

    def bind_runtime(self, runtime: Runtime) -> None:
        """绑定 Runtime 实例。

        Args:
            runtime: Agent Runtime 实例。
        """
        self._runtime = runtime

    def _ensure_root(self) -> None:
        """校验管理器是否属于 root 角色。

        Raises:
            RuntimeError: 当管理器不属于 root 时抛出。
        """
        if self._owner_role != "root":
            raise RuntimeError("Background tasks are only supported from the root agent.")

    def _ensure_local_backend(self) -> None:
        """校验会话是否为本地 backend。

        Raises:
            RuntimeError: 当会话非本地 backend 时抛出。
        """
        if self._session.work_dir_meta.kaos != local_kaos.name:
            raise RuntimeError("Background tasks are only supported on local sessions.")

    def _active_task_count(self) -> int:
        """获取活跃任务数量。

        Returns:
            非终态任务的数量。
        """
        return sum(
            1 for view in self._store.list_views() if not is_terminal_status(view.runtime.status)
        )

    def _worker_command(self, task_dir: Path) -> list[str]:
        """构建 Worker 进程启动命令。

        Args:
            task_dir: 任务目录路径。

        Returns:
            Worker 进程启动命令参数列表。
        """
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "__background-task-worker",
                "--task-dir",
                str(task_dir),
                "--heartbeat-interval-ms",
                str(self._config.worker_heartbeat_interval_ms),
                "--control-poll-interval-ms",
                str(self._config.wait_poll_interval_ms),
                "--kill-grace-period-ms",
                str(self._config.kill_grace_period_ms),
            ]
        return [
            sys.executable,
            "-m",
            "novel_cli.cli",
            "__background-task-worker",
            "--task-dir",
            str(task_dir),
            "--heartbeat-interval-ms",
            str(self._config.worker_heartbeat_interval_ms),
            "--control-poll-interval-ms",
            str(self._config.wait_poll_interval_ms),
            "--kill-grace-period-ms",
            str(self._config.kill_grace_period_ms),
        ]

    def _launch_worker(self, task_dir: Path) -> int:
        """启动 Worker 进程。

        Args:
            task_dir: 任务目录路径。

        Returns:
            Worker 进程 PID。
        """
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(task_dir),
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True

        process = subprocess.Popen(self._worker_command(task_dir), **kwargs)
        return process.pid

    def create_bash_task(
        self,
        *,
        command: str,
        description: str,
        timeout_s: int,
        tool_call_id: str,
        shell_name: str,
        shell_path: str,
        cwd: str,
    ) -> TaskView:
        """创建 Bash 类型的后台任务。

        Args:
            command: 要执行的命令。
            description: 任务描述。
            timeout_s: 超时时间（秒）。
            tool_call_id: tool_call ID。
            shell_name: Shell 名称。
            shell_path: Shell 可执行文件路径。
            cwd: 工作目录。

        Returns:
            创建的任务视图。

        Raises:
            RuntimeError: 当超出最大任务数限制时抛出。
        """
        self._ensure_root()
        self._ensure_local_backend()

        if self._active_task_count() >= self._config.max_running_tasks:
            raise RuntimeError("Too many background tasks are already running.")

        task_id = generate_task_id("bash")
        spec = TaskSpec(
            id=task_id,
            kind="bash",
            session_id=self._session.id,
            description=description,
            tool_call_id=tool_call_id,
            owner_role="root",
            command=command,
            shell_name=shell_name,
            shell_path=shell_path,
            cwd=cwd,
            timeout_s=timeout_s,
        )
        self._store.create_task(spec)

        runtime = self._store.read_runtime(task_id)
        task_dir = self._store.task_dir(task_id)
        try:
            worker_pid = self._launch_worker(task_dir)
        except Exception as exc:
            runtime.status = "failed"
            runtime.failure_reason = f"Failed to launch worker: {exc}"
            runtime.finished_at = time.time()
            runtime.updated_at = runtime.finished_at
            self._store.write_runtime(task_id, runtime)
            raise

        runtime = self._store.read_runtime(task_id)
        if runtime.finished_at is None and (
            runtime.status == "created"
            or (runtime.status == "starting" and runtime.worker_pid is None)
        ):
            runtime.status = "starting"
            runtime.worker_pid = worker_pid
            runtime.updated_at = time.time()
            self._store.write_runtime(task_id, runtime)
        return self._store.merged_view(task_id)

    def create_agent_task(
        self,
        *,
        agent_id: str,
        subagent_type: str,
        prompt: str,
        description: str,
        tool_call_id: str,
        model_override: str | None,
        timeout_s: int | None = None,
        resumed: bool = False,
    ) -> TaskView:
        """创建 Agent 类型的后台任务。

        Args:
            agent_id: Agent ID。
            subagent_type: 子 agent 类型。
            prompt: 执行提示词。
            description: 任务描述。
            tool_call_id: tool_call ID。
            model_override: 模型覆盖配置。
            timeout_s: 超时时间（秒）。
            resumed: 是否为恢复执行。

        Returns:
            创建的任务视图。

        Raises:
            RuntimeError: 当未绑定 Runtime 或超出最大任务数时抛出。
        """
        from .agent_runner import BackgroundAgentRunner

        self._ensure_root()
        self._ensure_local_backend()
        if self._runtime is None:
            raise RuntimeError("Background task manager is not bound to a runtime.")
        if self._active_task_count() >= self._config.max_running_tasks:
            raise RuntimeError("Too many background tasks are already running.")

        task_id = generate_task_id("agent")
        spec = TaskSpec(
            id=task_id,
            kind="agent",
            session_id=self._session.id,
            description=description,
            tool_call_id=tool_call_id,
            owner_role="root",
            kind_payload={
                "agent_id": agent_id,
                "subagent_type": subagent_type,
                "prompt": prompt,
                "model_override": model_override,
                "launch_mode": "background",
            },
        )
        self._store.create_task(spec)
        runtime = self._store.read_runtime(task_id)
        runtime.status = "starting"
        runtime.updated_at = time.time()
        self._store.write_runtime(task_id, runtime)
        effective_timeout = timeout_s or self._config.agent_task_timeout_s
        task = asyncio.create_task(
            BackgroundAgentRunner(
                runtime=self._runtime,
                manager=self,
                task_id=task_id,
                agent_id=agent_id,
                subagent_type=subagent_type,
                prompt=prompt,
                model_override=model_override,
                timeout_s=effective_timeout,
                resumed=resumed,
            ).run()
        )
        self._live_agent_tasks[task_id] = task
        return self._store.merged_view(task_id)

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        limit: int | None = 20,
    ) -> list[TaskView]:
        """列出任务视图。

        Args:
            status: 可选的状态过滤条件。
            limit: 返回结果的最大数量。

        Returns:
            任务视图列表。
        """
        tasks = self._store.list_views()
        if status is not None:
            tasks = [task for task in tasks if task.runtime.status == status]
        if limit is None:
            return tasks
        return tasks[:limit]

    def get_task(self, task_id: str) -> TaskView | None:
        """获取单个任务视图。

        Args:
            task_id: 任务 ID。

        Returns:
            任务视图，不存在时返回 None。
        """
        try:
            return self._store.merged_view(task_id)
        except (FileNotFoundError, ValueError):
            return None

    def resolve_output_path(self, task_id: str) -> Path:
        """获取任务的输出文件路径。

        Args:
            task_id: 任务 ID。

        Returns:
            输出文件路径。
        """
        return self._store.output_path(task_id)

    def read_output(
        self,
        task_id: str,
        *,
        offset: int = 0,
        max_bytes: int | None = None,
    ) -> TaskOutputChunk:
        """读取任务输出内容。

        Args:
            task_id: 任务 ID。
            offset: 读取起始偏移量。
            max_bytes: 最大读取字节数。

        Returns:
            输出块对象。
        """
        view = self._store.merged_view(task_id)
        return self._store.read_output(
            task_id,
            offset,
            max_bytes or self._config.read_max_bytes,
            status=view.runtime.status,
        )

    def tail_output(
        self,
        task_id: str,
        *,
        max_bytes: int | None = None,
        max_lines: int | None = None,
    ) -> str:
        """读取任务输出尾部内容。

        Args:
            task_id: 任务 ID。
            max_bytes: 最大读取字节数。
            max_lines: 最大返回行数。

        Returns:
            输出尾部文本。
        """
        self._store.merged_view(task_id)
        return self._store.tail_output(
            task_id,
            max_bytes=max_bytes or self._config.read_max_bytes,
            max_lines=max_lines or self._config.notification_tail_lines,
        )

    async def wait(self, task_id: str, *, timeout_s: int = 30) -> TaskView:
        """等待任务进入终态。

        Args:
            task_id: 任务 ID。
            timeout_s: 等待超时时间（秒）。

        Returns:
            任务视图。
        """
        end_time = time.monotonic() + timeout_s
        while True:
            view = self._store.merged_view(task_id)
            if is_terminal_status(view.runtime.status):
                return view
            if time.monotonic() >= end_time:
                return view
            await asyncio.sleep(self._config.wait_poll_interval_ms / 1000)

    def _best_effort_kill(self, runtime: TaskRuntime) -> None:
        """尝试终止任务进程（不保证成功）。

        Args:
            runtime: 任务运行状态。
        """
        try:
            if os.name == "nt":
                pid = runtime.child_pid or runtime.worker_pid
                if pid is None:
                    return
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                return

            if runtime.child_pgid is not None:
                os.killpg(runtime.child_pgid, signal.SIGTERM)
                return
            if runtime.child_pid is not None:
                os.kill(runtime.child_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception("Failed to send best-effort kill signal")

    def kill(self, task_id: str, *, reason: str = "Killed by user") -> TaskView:
        """终止指定任务。

        Args:
            task_id: 任务 ID。
            reason: 终止原因描述。

        Returns:
            更新后的任务视图。
        """
        self._ensure_root()
        view = self._store.merged_view(task_id)
        if is_terminal_status(view.runtime.status):
            return view

        if view.spec.kind == "agent":
            self._mark_task_killed(task_id, reason)
            if self._runtime is not None and self._runtime.approval_runtime is not None:
                self._runtime.approval_runtime.cancel_by_source("background_agent", task_id)
            task = self._live_agent_tasks.pop(task_id, None)
            if task is not None:
                task.cancel()
            return self._store.merged_view(task_id)

        control = view.control.model_copy(
            update={
                "kill_requested_at": time.time(),
                "kill_reason": reason,
                "force": False,
            }
        )
        self._store.write_control(task_id, control)
        self._best_effort_kill(view.runtime)
        return self._store.merged_view(task_id)

    def kill_all_active(self, *, reason: str = "CLI session ended") -> list[str]:
        """终止所有活跃任务。

        用于 CLI 关闭时清理所有非终态任务。

        Args:
            reason: 终止原因描述。

        Returns:
            已终止的任务 ID 列表。
        """
        killed: list[str] = []
        for view in self._store.list_views():
            if is_terminal_status(view.runtime.status):
                continue
            try:
                self.kill(view.spec.id, reason=reason)
                killed.append(view.spec.id)
            except Exception:
                logger.exception(
                    "Failed to kill task {task_id} during shutdown",
                    task_id=view.spec.id,
                )
        return killed

    def recover(self) -> None:
        """恢复任务状态。

        检查所有非终态任务，将心跳过期或进程丢失的任务标记为 lost 或 killed。
        """
        now = time.time()
        stale_after = self._config.worker_stale_after_ms / 1000
        for view in self._store.list_views():
            if is_terminal_status(view.runtime.status):
                continue
            if view.spec.kind == "agent":
                if view.spec.id in self._live_agent_tasks:
                    continue
                runtime = view.runtime.model_copy()
                runtime.finished_at = now
                runtime.updated_at = now
                runtime.status = "lost"
                runtime.failure_reason = "In-process background agent is no longer running"
                self._store.write_runtime(view.spec.id, runtime)
                agent_id = (view.spec.kind_payload or {}).get("agent_id")
                if (
                    isinstance(agent_id, str)
                    and self._runtime is not None
                    and self._runtime.subagent_store is not None
                ):
                    record = self._runtime.subagent_store.get_instance(agent_id)
                    if record is not None and record.status == "running_background":
                        self._runtime.subagent_store.update_instance(agent_id, status="failed")
                continue
            last_progress_at = (
                view.runtime.heartbeat_at
                or view.runtime.started_at
                or view.runtime.updated_at
                or view.spec.created_at
            )
            if now - last_progress_at <= stale_after:
                continue

            # 重新读取运行状态以缩小与 Worker 进程的竞态窗口
            fresh_runtime = self._store.read_runtime(view.spec.id)
            if is_terminal_status(fresh_runtime.status):
                continue
            fresh_progress = (
                fresh_runtime.heartbeat_at
                or fresh_runtime.started_at
                or fresh_runtime.updated_at
                or view.spec.created_at
            )
            if now - fresh_progress <= stale_after:
                continue

            runtime = fresh_runtime.model_copy()
            runtime.finished_at = now
            runtime.updated_at = now
            if view.control.kill_requested_at is not None:
                runtime.status = "killed"
                runtime.interrupted = True
                runtime.failure_reason = view.control.kill_reason or "Killed during recovery"
            else:
                runtime.status = "lost"
                runtime.failure_reason = (
                    "Background worker never heartbeat after startup"
                    if fresh_runtime.heartbeat_at is None
                    else "Background worker heartbeat expired"
                )
            self._store.write_runtime(view.spec.id, runtime)

    def reconcile(self, *, limit: int | None = None) -> list[str]:
        """执行任务恢复并发布终态通知。

        Args:
            limit: 最大通知数量。

        Returns:
            已发布的通知 ID 列表。
        """
        self.recover()
        return self.publish_terminal_notifications(limit=limit)

    def publish_terminal_notifications(self, *, limit: int | None = None) -> list[str]:
        """发布终态任务通知。

        Args:
            limit: 最大通知数量。

        Returns:
            已发布的通知 ID 列表。
        """
        published: list[str] = []
        for view in self._store.list_views():
            if not is_terminal_status(view.runtime.status):
                continue

            status = view.runtime.status
            terminal_reason = "timed_out" if view.runtime.timed_out else status
            match terminal_reason:
                case "completed":
                    severity = "success"
                    title = f"Background task completed: {view.spec.description}"
                case "timed_out":
                    severity = "error"
                    title = f"Background task timed out: {view.spec.description}"
                case "failed":
                    severity = "error"
                    title = f"Background task failed: {view.spec.description}"
                case "killed":
                    severity = "warning"
                    title = f"Background task stopped: {view.spec.description}"
                case "lost":
                    severity = "warning"
                    title = f"Background task lost: {view.spec.description}"
                case _:
                    severity = "info"
                    title = f"Background task updated: {view.spec.description}"

            body_lines = [
                f"Task ID: {view.spec.id}",
                f"Status: {status}",
                f"Description: {view.spec.description}",
            ]
            if terminal_reason != status:
                body_lines.append(f"Terminal reason: {terminal_reason}")
            if view.runtime.exit_code is not None:
                body_lines.append(f"Exit code: {view.runtime.exit_code}")
            if view.runtime.failure_reason:
                body_lines.append(f"Failure reason: {view.runtime.failure_reason}")

            event = NotificationEvent(
                id=self._notifications.new_id(),
                category="task",
                type=f"task.{terminal_reason}",
                source_kind="background_task",
                source_id=view.spec.id,
                title=title,
                body="\n".join(body_lines),
                severity=severity,
                payload={
                    "task_id": view.spec.id,
                    "task_kind": view.spec.kind,
                    "status": status,
                    "description": view.spec.description,
                    "exit_code": view.runtime.exit_code,
                    "interrupted": view.runtime.interrupted,
                    "timed_out": view.runtime.timed_out,
                    "terminal_reason": terminal_reason,
                    "failure_reason": view.runtime.failure_reason,
                },
                dedupe_key=f"background_task:{view.spec.id}:{terminal_reason}",
            )
            notification = self._notifications.publish(event)
            if notification.event.id == event.id:
                published.append(notification.event.id)
                self._completion_event.set()
            if limit is not None and len(published) >= limit:
                break
        return published

    def _mark_task_running(self, task_id: str) -> None:
        """将任务状态标记为 running。

        Args:
            task_id: 任务 ID。
        """
        runtime = self._store.read_runtime(task_id)
        if is_terminal_status(runtime.status):
            return
        runtime.status = "running"
        runtime.updated_at = time.time()
        runtime.heartbeat_at = runtime.updated_at
        runtime.failure_reason = None
        self._store.write_runtime(task_id, runtime)

    def _mark_task_awaiting_approval(self, task_id: str, reason: str) -> None:
        """将任务状态标记为 awaiting_approval。

        Args:
            task_id: 任务 ID。
            reason: 等待审批的原因描述。
        """
        runtime = self._store.read_runtime(task_id)
        if is_terminal_status(runtime.status):
            return
        runtime.status = "awaiting_approval"
        runtime.updated_at = time.time()
        runtime.failure_reason = reason
        self._store.write_runtime(task_id, runtime)

    def _mark_task_completed(self, task_id: str) -> None:
        """将任务状态标记为 completed。

        Args:
            task_id: 任务 ID。
        """
        runtime = self._store.read_runtime(task_id)
        if is_terminal_status(runtime.status):
            return
        runtime.status = "completed"
        runtime.updated_at = time.time()
        runtime.finished_at = runtime.updated_at
        runtime.failure_reason = None
        self._store.write_runtime(task_id, runtime)

    def _mark_task_failed(self, task_id: str, reason: str) -> None:
        """将任务状态标记为 failed。

        Args:
            task_id: 任务 ID。
            reason: 失败原因描述。
        """
        runtime = self._store.read_runtime(task_id)
        if is_terminal_status(runtime.status):
            return
        runtime.status = "failed"
        runtime.updated_at = time.time()
        runtime.finished_at = runtime.updated_at
        runtime.failure_reason = reason
        self._store.write_runtime(task_id, runtime)

    def _mark_task_timed_out(self, task_id: str, reason: str) -> None:
        """将任务状态标记为 timed_out（内部状态为 failed + timed_out=True）。

        Args:
            task_id: 任务 ID。
            reason: 超时原因描述。
        """
        runtime = self._store.read_runtime(task_id)
        if is_terminal_status(runtime.status):
            return
        runtime.status = "failed"
        runtime.updated_at = time.time()
        runtime.finished_at = runtime.updated_at
        runtime.interrupted = True
        runtime.timed_out = True
        runtime.failure_reason = reason
        self._store.write_runtime(task_id, runtime)

    def _mark_task_killed(self, task_id: str, reason: str) -> None:
        """将任务状态标记为 killed。

        Args:
            task_id: 任务 ID。
            reason: 终止原因描述。
        """
        runtime = self._store.read_runtime(task_id)
        if is_terminal_status(runtime.status):
            return
        runtime.status = "killed"
        runtime.updated_at = time.time()
        runtime.finished_at = runtime.updated_at
        runtime.interrupted = True
        runtime.failure_reason = reason
        self._store.write_runtime(task_id, runtime)
