"""待办事项管理工具实现。

提供 SetTodoList 工具，用于管理待办事项列表，
支持读取和更新待办事项，并区分根代理和子代理的存储方式。
"""

import json
from pathlib import Path
from typing import Any, Literal, cast, override

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.session_state import TodoItemState
from novel_cli.soul.agent import Runtime
from novel_cli.tools.display import TodoDisplayBlock, TodoDisplayItem
from novel_cli.tools.utils import load_desc
from novel_cli.utils.logging import logger


class Todo(BaseModel):
    """待办事项模型。

    Attributes:
        title: 待办事项标题。
        status: 待办事项状态，可选值为 pending、in_progress、done。
    """

    title: str = Field(description="The title of the todo", min_length=1)
    status: Literal["pending", "in_progress", "done"] = Field(description="The status of the todo")


class Params(BaseModel):
    """待办列表参数。

    Attributes:
        todos: 更新的待办列表。如果不提供，则返回当前待办列表而不做任何更改。
    """

    todos: list[Todo] | None = Field(
        default=None,
        description=(
            "The updated todo list. "
            "If not provided, returns the current todo list without making changes."
        ),
    )


class SetTodoList(CallableTool2[Params]):
    """待办列表管理工具。

    用于管理待办事项列表，支持读取和更新两种模式：
    1. 读取模式：不提供 todos 参数时返回当前待办列表
    2. 更新模式：提供 todos 参数时更新待办列表

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "SetTodoList"
    description: str = load_desc(Path(__file__).parent / "set_todo_list.md")
    params: type[Params] = Params

    def __init__(self, runtime: Runtime) -> None:
        """初始化待办列表工具。

        Args:
            runtime: 运行时环境，包含会话状态等信息。
        """
        super().__init__()
        self._runtime = runtime

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行待办列表操作。

        Args:
            params: 待办列表参数。

        Returns:
            操作结果，当前待办列表或更新确认信息。
        """
        if params.todos is None:
            return self._read_todos()
        return self._write_todos(params.todos)

    # ---- 写入模式 --------------------------------------------------------

    def _write_todos(self, todos: list[Todo]) -> ToolReturnValue:
        """持久化待办列表并返回确认信息。

        Args:
            todos: 待办列表。

        Returns:
            操作结果，包含更新确认和显示块。
        """
        self._save_todos(todos)

        items = [TodoDisplayItem(title=todo.title, status=todo.status) for todo in todos]
        return ToolReturnValue(
            is_error=False,
            output="Todo list updated",
            message="Todo list updated",
            display=[TodoDisplayBlock(items=items)],
        )

    # ---- 读取模式 ---------------------------------------------------------

    def _read_todos(self) -> ToolReturnValue:
        """读取当前待办列表并返回文本输出。

        Returns:
            操作结果，包含当前待办列表的文本表示。
        """
        todos = self._load_todos()
        if not todos:
            return ToolReturnValue(
                is_error=False,
                output="Todo list is empty.",
                message="",
                display=[],
            )

        lines: list[str] = ["Current todo list:"]
        for todo in todos:
            lines.append(f"- [{todo.status}] {todo.title}")
        return ToolReturnValue(
            is_error=False,
            output="\n".join(lines),
            message="",
            display=[],
        )

    # ---- 持久化 -------------------------------------------------------

    def _save_todos(self, todos: list[Todo]) -> None:
        """将待办列表持久化到相应的状态文件。

        Args:
            todos: 待办列表。
        """
        items = [TodoItemState(title=t.title, status=t.status) for t in todos]

        if self._runtime.role == "root":
            self._save_root_todos(items)
        else:
            self._save_subagent_todos(items)

    def _load_todos(self) -> list[Todo]:
        """从相应的状态文件加载待办列表。

        Returns:
            加载的待办列表。
        """
        if self._runtime.role == "root":
            return self._load_root_todos()
        else:
            return self._load_subagent_todos()

    def _save_root_todos(self, items: list[TodoItemState]) -> None:
        """保存根代理的待办列表到会话状态。

        Args:
            items: 待办事项状态列表。
        """
        session = self._runtime.session
        session.state.todos = items
        session.save_state()

    def _load_root_todos(self) -> list[Todo]:
        """从会话状态加载根代理的待办列表。

        Returns:
            加载的待办列表。
        """
        from novel_cli.session_state import load_session_state

        session = self._runtime.session
        fresh = load_session_state(session.dir)
        session.state.todos = fresh.todos
        result: list[Todo] = []
        for t in fresh.todos:
            try:
                result.append(Todo(title=t.title, status=t.status))
            except Exception:
                logger.warning("Skipping malformed todo item in root state: {t}", t=t)
        return result

    def _save_subagent_todos(self, items: list[TodoItemState]) -> None:
        """保存子代理的待办列表到状态文件。

        Args:
            items: 待办事项状态列表。
        """
        state_file = self._subagent_state_file()
        if state_file is None:
            return
        data = self._read_subagent_state(state_file)
        data["todos"] = [item.model_dump() for item in items]
        self._write_subagent_state(state_file, data)

    def _load_subagent_todos(self) -> list[Todo]:
        """从状态文件加载子代理的待办列表。

        Returns:
            加载的待办列表。
        """
        state_file = self._subagent_state_file()
        if state_file is None:
            return []
        data = self._read_subagent_state(state_file)
        raw_todos_val = data.get("todos", [])
        raw_todos = cast(list[Any], raw_todos_val) if isinstance(raw_todos_val, list) else []
        result: list[Todo] = []
        for item in raw_todos:
            try:
                result.append(Todo(**item))
            except Exception:
                logger.warning("Skipping malformed todo item in subagent state: {item}", item=item)
        return result

    def _subagent_state_file(self) -> Path | None:
        """获取子代理状态文件路径。

        Returns:
            状态文件路径，如果子代理存储或 ID 不存在则返回 None。
        """
        store = self._runtime.subagent_store
        agent_id = self._runtime.subagent_id
        if store is None or agent_id is None:
            return None
        return store.instance_dir(agent_id) / "state.json"

    @staticmethod
    def _read_subagent_state(path: Path) -> dict[str, Any]:
        """读取子代理状态文件。

        Args:
            path: 状态文件路径。

        Returns:
            状态数据字典，如果文件损坏或不存在则返回空字典。
        """
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            logger.warning("Corrupted subagent todo state, using defaults: {path}", path=path)
            return {}
        if not isinstance(data, dict):
            logger.warning("Invalid subagent todo state type, using defaults: {path}", path=path)
            return {}
        return cast(dict[str, Any], data)

    @staticmethod
    def _write_subagent_state(path: Path, data: dict[str, Any]) -> None:
        """写入子代理状态文件。

        Args:
            path: 状态文件路径。
            data: 状态数据字典。
        """
        from novel_cli.utils.io import atomic_json_write

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(data, path)
