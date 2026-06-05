"""后台任务存储模块。

提供基于文件系统的任务数据持久化功能，包括规格、运行状态、控制和输出文件的读写。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from novel_cli.utils.io import atomic_json_write
from novel_cli.utils.logging import logger

from .models import (
    TaskConsumerState,
    TaskControl,
    TaskOutputChunk,
    TaskRuntime,
    TaskSpec,
    TaskStatus,
    TaskView,
)

# 任务 ID 有效格式校验正则
_VALID_TASK_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{1,24}$")


def _validate_task_id(task_id: str) -> None:
    """校验任务 ID 格式是否有效。

    Args:
        task_id: 待校验的任务 ID。

    Raises:
        ValueError: 当任务 ID 格式无效时抛出。
    """
    if not _VALID_TASK_ID.match(task_id):
        raise ValueError(f"Invalid task_id: {task_id!r}")


class BackgroundTaskStore:
    """后台任务文件存储管理器。

    提供任务数据的文件系统持久化操作，包括创建、读取、写入和列表查询。

    Attributes:
        SPEC_FILE: 规格文件名。
        RUNTIME_FILE: 运行状态文件名。
        CONTROL_FILE: 控制文件名。
        CONSUMER_FILE: 消费者状态文件名。
        OUTPUT_FILE: 输出日志文件名。
    """

    SPEC_FILE = "spec.json"
    RUNTIME_FILE = "runtime.json"
    CONTROL_FILE = "control.json"
    CONSUMER_FILE = "consumer.json"
    OUTPUT_FILE = "output.log"

    def __init__(self, root: Path):
        """初始化存储管理器。

        Args:
            root: 任务存储根目录路径。
        """
        self._root = root

    @property
    def root(self) -> Path:
        """任务存储根目录。

        Returns:
            根目录路径对象。
        """
        return self._root

    def _ensure_root(self) -> Path:
        """确保根目录存在，不存在则创建。

        Returns:
            根目录路径对象。
        """
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def task_dir(self, task_id: str) -> Path:
        """获取任务目录路径，确保目录存在。

        Args:
            task_id: 任务 ID。

        Returns:
            任务目录路径对象。
        """
        _validate_task_id(task_id)
        path = self._ensure_root() / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def task_path(self, task_id: str) -> Path:
        """获取任务目录路径（不创建）。

        Args:
            task_id: 任务 ID。

        Returns:
            任务目录路径对象。
        """
        _validate_task_id(task_id)
        return self.root / task_id

    def spec_path(self, task_id: str) -> Path:
        """获取任务规格文件路径。

        Args:
            task_id: 任务 ID。

        Returns:
            规格文件路径对象。
        """
        return self.task_path(task_id) / self.SPEC_FILE

    def runtime_path(self, task_id: str) -> Path:
        """获取任务运行状态文件路径。

        Args:
            task_id: 任务 ID。

        Returns:
            运行状态文件路径对象。
        """
        return self.task_path(task_id) / self.RUNTIME_FILE

    def control_path(self, task_id: str) -> Path:
        """获取任务控制文件路径。

        Args:
            task_id: 任务 ID。

        Returns:
            控制文件路径对象。
        """
        return self.task_path(task_id) / self.CONTROL_FILE

    def consumer_path(self, task_id: str) -> Path:
        """获取任务消费者状态文件路径。

        Args:
            task_id: 任务 ID。

        Returns:
            消费者状态文件路径对象。
        """
        return self.task_path(task_id) / self.CONSUMER_FILE

    def output_path(self, task_id: str) -> Path:
        """获取任务输出日志文件路径。

        Args:
            task_id: 任务 ID。

        Returns:
            输出日志文件路径对象。
        """
        return self.task_path(task_id) / self.OUTPUT_FILE

    def create_task(self, spec: TaskSpec) -> None:
        """创建新任务及其相关文件。

        Args:
            spec: 任务规格数据。
        """
        task_dir = self.task_dir(spec.id)
        atomic_json_write(spec.model_dump(mode="json"), task_dir / self.SPEC_FILE)
        atomic_json_write(TaskRuntime().model_dump(mode="json"), task_dir / self.RUNTIME_FILE)
        atomic_json_write(TaskControl().model_dump(mode="json"), task_dir / self.CONTROL_FILE)
        atomic_json_write(
            TaskConsumerState().model_dump(mode="json"),
            task_dir / self.CONSUMER_FILE,
        )
        self.output_path(spec.id).touch(exist_ok=True)

    def list_task_ids(self) -> list[str]:
        """列出所有任务 ID。

        Returns:
            按名称排序的任务 ID 列表。
        """
        if not self.root.exists():
            return []
        task_ids: list[str] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir():
                continue
            if not (path / self.SPEC_FILE).exists():
                continue
            task_ids.append(path.name)
        return task_ids

    def write_spec(self, spec: TaskSpec) -> None:
        """写入任务规格数据。

        Args:
            spec: 任务规格数据。
        """
        atomic_json_write(spec.model_dump(mode="json"), self.spec_path(spec.id))

    def read_spec(self, task_id: str) -> TaskSpec:
        """读取任务规格数据。

        Args:
            task_id: 任务 ID。

        Returns:
            任务规格数据对象。
        """
        return TaskSpec.model_validate_json(self.spec_path(task_id).read_text(encoding="utf-8"))

    def write_runtime(self, task_id: str, runtime: TaskRuntime) -> None:
        """写入任务运行状态数据。

        Args:
            task_id: 任务 ID。
            runtime: 运行状态数据。
        """
        atomic_json_write(runtime.model_dump(mode="json"), self.runtime_path(task_id))

    def read_runtime(self, task_id: str) -> TaskRuntime:
        """读取任务运行状态数据。

        Args:
            task_id: 任务 ID。

        Returns:
            运行状态数据对象，文件不存在时返回默认状态。
        """
        path = self.runtime_path(task_id)
        if not path.exists():
            return TaskRuntime()
        return _read_json_model(
            path,
            TaskRuntime,
            fallback=TaskRuntime(updated_at=0),
            artifact="task runtime",
        )

    def write_control(self, task_id: str, control: TaskControl) -> None:
        """写入任务控制数据。

        Args:
            task_id: 任务 ID。
            control: 控制数据。
        """
        atomic_json_write(control.model_dump(mode="json"), self.control_path(task_id))

    def read_control(self, task_id: str) -> TaskControl:
        """读取任务控制数据。

        Args:
            task_id: 任务 ID。

        Returns:
            控制数据对象，文件不存在时返回默认状态。
        """
        path = self.control_path(task_id)
        if not path.exists():
            return TaskControl()
        return _read_json_model(
            path,
            TaskControl,
            fallback=TaskControl(),
            artifact="task control",
        )

    def write_consumer(self, task_id: str, consumer: TaskConsumerState) -> None:
        """写入任务消费者状态数据。

        Args:
            task_id: 任务 ID。
            consumer: 消费者状态数据。
        """
        atomic_json_write(consumer.model_dump(mode="json"), self.consumer_path(task_id))

    def read_consumer(self, task_id: str) -> TaskConsumerState:
        """读取任务消费者状态数据。

        Args:
            task_id: 任务 ID。

        Returns:
            消费者状态数据对象，文件不存在时返回默认状态。
        """
        path = self.consumer_path(task_id)
        if not path.exists():
            return TaskConsumerState()
        return _read_json_model(
            path,
            TaskConsumerState,
            fallback=TaskConsumerState(),
            artifact="task consumer state",
        )

    def merged_view(self, task_id: str) -> TaskView:
        """聚合任务的所有状态数据为视图。

        Args:
            task_id: 任务 ID。

        Returns:
            包含规格、运行状态、控制和消费者状态的完整视图。
        """
        return TaskView(
            spec=self.read_spec(task_id),
            runtime=self.read_runtime(task_id),
            control=self.read_control(task_id),
            consumer=self.read_consumer(task_id),
        )

    def list_views(self) -> list[TaskView]:
        """列出所有任务的完整视图。

        Returns:
            按更新时间降序排列的任务视图列表。
        """
        views: list[TaskView] = []
        for task_id in self.list_task_ids():
            try:
                views.append(self.merged_view(task_id))
            except (OSError, ValidationError, ValueError, UnicodeDecodeError) as exc:
                logger.warning(
                    "Skipping invalid background task {task_id} from {path}: {error}",
                    task_id=task_id,
                    path=self.root / task_id / self.SPEC_FILE,
                    error=exc,
                )
        views.sort(
            key=lambda view: view.runtime.updated_at or view.spec.created_at,
            reverse=True,
        )
        return views

    def read_output(
        self,
        task_id: str,
        offset: int,
        max_bytes: int,
        *,
        status: TaskStatus,
        path_override: Path | None = None,
    ) -> TaskOutputChunk:
        """读取任务输出内容。

        Args:
            task_id: 任务 ID。
            offset: 读取起始偏移量（字节）。
            max_bytes: 最大读取字节数。
            status: 当前任务状态。
            path_override: 可选的输出文件路径覆盖。

        Returns:
            包含输出内容和偏移信息的输出块对象。
        """
        path = path_override if path_override is not None else self.output_path(task_id)
        if not path.exists():
            return TaskOutputChunk(
                task_id=task_id,
                offset=offset,
                next_offset=offset,
                text="",
                eof=True,
                status=status,
            )

        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            total_size = f.tell()
            bounded_offset = min(max(offset, 0), total_size)
            f.seek(bounded_offset)
            content = f.read(max_bytes)

        next_offset = bounded_offset + len(content)
        return TaskOutputChunk(
            task_id=task_id,
            offset=bounded_offset,
            next_offset=next_offset,
            text=content.decode("utf-8", errors="replace"),
            eof=next_offset >= total_size,
            status=status,
        )

    def tail_output(self, task_id: str, max_bytes: int, max_lines: int) -> str:
        """读取任务输出的尾部内容。

        Args:
            task_id: 任务 ID。
            max_bytes: 最大读取字节数。
            max_lines: 最大返回行数。

        Returns:
            输出尾部文本内容。
        """
        path = self.output_path(task_id)
        if not path.exists():
            return ""

        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            total_size = f.tell()
            start = max(0, total_size - max_bytes)
            f.seek(start)
            content = f.read()

        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return "\n".join(lines)


def _read_json_model[T: BaseModel](path: Path, model: type[T], *, fallback: T, artifact: str) -> T:
    """读取 JSON 文件并解析为 Pydantic 模型，失败时返回默认值。

    Args:
        path: JSON 文件路径。
        model: 目标 Pydantic 模型类型。
        fallback: 解析失败时的默认返回值。
        artifact: 数据类型描述（用于日志）。

    Returns:
        解析成功时返回模型实例，失败时返回 fallback。
    """
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError, UnicodeDecodeError) as exc:
        logger.warning(
            "Failed to read {artifact} from {path}; using defaults: {error}",
            artifact=artifact,
            path=path,
            error=exc,
        )
        return fallback
