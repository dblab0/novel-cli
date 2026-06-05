"""后台任务管理工具集。

提供 TaskList、TaskOutput、TaskStop 三个工具，用于列出、查看和停止后台任务。
"""

import time
from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolError, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.background import TaskView, format_task, format_task_list, list_task_views
from novel_cli.soul.agent import Runtime
from novel_cli.soul.approval import Approval
from novel_cli.tools.display import BackgroundTaskDisplayBlock
from novel_cli.tools.utils import load_desc

# 任务输出预览字节大小（32KB）
TASK_OUTPUT_PREVIEW_BYTES = 32 << 10
# 任务输出读取提示行数
TASK_OUTPUT_READ_HINT_LINES = 300


def _ensure_root(runtime: Runtime) -> ToolError | None:
    """检查当前角色是否为 root 智能体。

    后台任务只能由 root 智能体管理。

    Args:
        runtime: 当前运行环境。

    Returns:
        若角色不是 root 则返回错误，否则返回 None。
    """
    if runtime.role != "root":
        return ToolError(
            message="Background tasks can only be managed by the root agent.",
            brief="Background task unavailable",
        )
    return None


def _task_display(runtime: Runtime, task_id: str) -> BackgroundTaskDisplayBlock:
    """创建后台任务显示块。

    Args:
        runtime: 当前运行环境。
        task_id: 任务 ID。

    Returns:
        后台任务显示块对象。
    """
    view = runtime.background_tasks.store.merged_view(task_id)
    return BackgroundTaskDisplayBlock(
        task_id=view.spec.id,
        kind=view.spec.kind,
        status=view.runtime.status,
        description=view.spec.description,
    )


def _format_task_output(
    view: TaskView,
    *,
    retrieval_status: str,
    output: str,
    output_path: Path,
    full_output_available: bool,
    output_size_bytes: int,
    output_preview_bytes: int,
    output_truncated: bool,
) -> str:
    """格式化任务输出信息。

    Args:
        view: 任务视图对象。
        retrieval_status: 检索状态（success/timeout/not_ready）。
        output: 输出内容预览。
        output_path: 输出文件路径。
        full_output_available: 是否有完整输出文件可用。
        output_size_bytes: 输出文件大小（字节）。
        output_preview_bytes: 预览字节大小。
        output_truncated: 输出是否被截断。

    Returns:
        格式化后的任务输出信息字符串。
    """
    terminal_reason = "timed_out" if view.runtime.timed_out else view.runtime.status
    output_path_str = str(output_path.resolve())
    lines = [
        f"retrieval_status: {retrieval_status}",
        f"task_id: {view.spec.id}",
        f"kind: {view.spec.kind}",
        f"status: {view.runtime.status}",
        f"description: {view.spec.description}",
    ]
    if view.spec.kind == "agent" and view.spec.kind_payload:
        if agent_id := view.spec.kind_payload.get("agent_id"):
            lines.append(f"agent_id: {agent_id}")
        if subagent_type := view.spec.kind_payload.get("subagent_type"):
            lines.append(f"subagent_type: {subagent_type}")
    if view.spec.command:
        lines.append(f"command: {view.spec.command}")
    lines.extend(
        [
            f"interrupted: {str(view.runtime.interrupted).lower()}",
            f"timed_out: {str(view.runtime.timed_out).lower()}",
            f"terminal_reason: {terminal_reason}",
        ]
    )
    if view.runtime.exit_code is not None:
        lines.append(f"exit_code: {view.runtime.exit_code}")
    if view.runtime.failure_reason:
        lines.append(f"reason: {view.runtime.failure_reason}")
    full_output_hint = (
        (
            "full_output_hint: "
            f'Use ReadFile(path="{output_path_str}", line_offset=1, '
            f"n_lines={TASK_OUTPUT_READ_HINT_LINES}) to inspect the full log. "
            "Increase line_offset to continue paging through the file."
        )
        if full_output_available
        else "full_output_hint: No output file is currently available for this task."
    )
    lines.extend(
        [
            "",
            f"output_path: {output_path_str}",
            f"output_size_bytes: {output_size_bytes}",
            f"output_preview_bytes: {output_preview_bytes}",
            f"output_truncated: {str(output_truncated).lower()}",
            "",
            f"full_output_available: {str(full_output_available).lower()}",
            "full_output_tool: ReadFile",
            full_output_hint,
        ]
    )
    rendered_output = output or "[no output available]"
    if output_truncated:
        rendered_output = f"[Truncated. Full output: {output_path_str}]\n\n{rendered_output}"
    return "\n".join(
        lines
        + [
            "",
            "[output]",
            rendered_output,
        ]
    )


class TaskOutputParams(BaseModel):
    """TaskOutput 工具的参数模型。

    Attributes:
        task_id: 要查看的后台任务 ID。
        block: 是否等待任务完成后返回。
        timeout: block=true 时的最大等待秒数。
    """

    task_id: str = Field(description="The background task ID to inspect.")
    block: bool = Field(
        default=False,
        description="Whether to wait for the task to finish before returning.",
    )
    timeout: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="Maximum number of seconds to wait when block=true.",
    )


class TaskStopParams(BaseModel):
    """TaskStop 工具的参数模型。

    Attributes:
        task_id: 要停止的后台任务 ID。
        reason: 任务停止时记录的简短原因。
    """

    task_id: str = Field(description="The background task ID to stop.")
    reason: str = Field(
        default="Stopped by TaskStop",
        description="Short reason recorded when the task is stopped.",
    )


class TaskListParams(BaseModel):
    """TaskList 工具的参数模型。

    Attributes:
        active_only: 是否只列出非终止状态的后台任务。
        limit: 返回任务的最大数量。
    """

    active_only: bool = Field(
        default=True,
        description="Whether to list only non-terminal background tasks.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of tasks to return.",
    )


class TaskList(CallableTool2[TaskListParams]):
    """列出后台任务的工具。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "TaskList"
    description: str = load_desc(Path(__file__).parent / "list.md")
    params: type[TaskListParams] = TaskListParams

    def __init__(self, runtime: Runtime):
        """初始化 TaskList 工具。

        Args:
            runtime: 当前运行环境。
        """
        super().__init__()
        self._runtime = runtime

    @override
    async def __call__(self, params: TaskListParams) -> ToolReturnValue:
        """执行任务列表查询。

        Args:
            params: 查询参数。

        Returns:
            任务列表查询结果。
        """
        if err := _ensure_root(self._runtime):
            return err

        views = list_task_views(
            self._runtime.background_tasks,
            active_only=params.active_only,
            limit=params.limit,
        )
        display = [
            BackgroundTaskDisplayBlock(
                task_id=view.spec.id,
                kind=view.spec.kind,
                status=view.runtime.status,
                description=view.spec.description,
            )
            for view in views
        ]
        return ToolReturnValue(
            is_error=False,
            output=format_task_list(views, active_only=params.active_only),
            message="Task list retrieved.",
            display=list(display),
        )


class TaskOutput(CallableTool2[TaskOutputParams]):
    """获取后台任务输出的工具。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "TaskOutput"
    description: str = load_desc(Path(__file__).parent / "output.md")
    params: type[TaskOutputParams] = TaskOutputParams

    def __init__(self, runtime: Runtime):
        """初始化 TaskOutput 工具。

        Args:
            runtime: 当前运行环境。
        """
        super().__init__()
        self._runtime = runtime

    def _render_output_preview(self, task_id: str) -> tuple[str, bool, int, int, bool, Path]:
        """渲染任务输出预览。

        Args:
            task_id: 任务 ID。

        Returns:
            元组，包含：输出内容、是否有完整输出、输出大小、预览字节、是否截断、输出路径。
        """
        manager = self._runtime.background_tasks
        output_path = manager.resolve_output_path(task_id)
        try:
            output_size = output_path.stat().st_size if output_path.exists() else 0
        except OSError:
            output_size = 0
        # 从文件末尾读取预览内容
        preview_offset = max(0, output_size - TASK_OUTPUT_PREVIEW_BYTES)
        chunk = manager.read_output(
            task_id,
            offset=preview_offset,
            max_bytes=TASK_OUTPUT_PREVIEW_BYTES,
        )
        return (
            chunk.text.rstrip("\n"),
            output_size > 0,
            output_size,
            chunk.next_offset - chunk.offset,
            preview_offset > 0,
            output_path,
        )

    @override
    async def __call__(self, params: TaskOutputParams) -> ToolReturnValue:
        """执行任务输出查询。

        Args:
            params: 查询参数。

        Returns:
            任务输出查询结果。
        """
        if err := _ensure_root(self._runtime):
            return err

        view = self._runtime.background_tasks.get_task(params.task_id)
        if view is None:
            return ToolError(message=f"Task not found: {params.task_id}", brief="Task not found")

        # 若 block=true，等待任务完成
        if params.block:
            view = await self._runtime.background_tasks.wait(
                params.task_id,
                timeout_s=params.timeout,
            )
            retrieval_status = (
                "success"
                if view.runtime.status in {"completed", "failed", "killed", "lost"}
                else "timeout"
            )
        else:
            retrieval_status = (
                "success"
                if view.runtime.status in {"completed", "failed", "killed", "lost"}
                else "not_ready"
            )

        (
            output,
            full_output_available,
            output_size,
            output_preview_bytes,
            output_truncated,
            output_path,
        ) = self._render_output_preview(params.task_id)
        # 更新消费者的查看状态
        consumer = view.consumer.model_copy(
            update={
                "last_seen_output_size": output_size,
                "last_viewed_at": time.time(),
            }
        )
        self._runtime.background_tasks.store.write_consumer(params.task_id, consumer)

        return ToolReturnValue(
            is_error=False,
            output=_format_task_output(
                view,
                retrieval_status=retrieval_status,
                output=output,
                output_path=output_path,
                full_output_available=full_output_available,
                output_size_bytes=output_size,
                output_preview_bytes=output_preview_bytes,
                output_truncated=output_truncated,
            ),
            message=(
                "Task snapshot retrieved."
                if not params.block and retrieval_status == "not_ready"
                else "Task output retrieved."
            ),
            display=[_task_display(self._runtime, params.task_id)],
        )


class TaskStop(CallableTool2[TaskStopParams]):
    """停止后台任务的工具。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "TaskStop"
    description: str = load_desc(Path(__file__).parent / "stop.md")
    params: type[TaskStopParams] = TaskStopParams

    def __init__(self, runtime: Runtime, approval: Approval):
        """初始化 TaskStop 工具。

        Args:
            runtime: 当前运行环境。
            approval: 审批机制对象。
        """
        super().__init__()
        self._runtime = runtime
        self._approval = approval

    @override
    async def __call__(self, params: TaskStopParams) -> ToolReturnValue:
        """执行任务停止操作。

        Args:
            params: 停止参数。

        Returns:
            任务停止操作结果。
        """
        if err := _ensure_root(self._runtime):
            return err
        # 计划模式下不可使用 TaskStop
        if self._runtime.session.state.plan_mode:
            return ToolError(
                message="TaskStop is not available in plan mode.",
                brief="Blocked in plan mode",
            )

        view = self._runtime.background_tasks.get_task(params.task_id)
        if view is None:
            return ToolError(message=f"Task not found: {params.task_id}", brief="Task not found")

        # 请求用户审批
        result = await self._approval.request(
            self.name,
            "stop background task",
            f"Stop background task `{params.task_id}`",
            display=[_task_display(self._runtime, params.task_id)],
        )
        if not result:
            return result.rejection_error()

        # 执行停止操作
        view = self._runtime.background_tasks.kill(
            params.task_id,
            reason=params.reason.strip() or "Stopped by TaskStop",
        )
        return ToolReturnValue(
            is_error=False,
            output=format_task(view, include_command=True),
            message="Task stop requested.",
            display=[_task_display(self._runtime, params.task_id)],
        )