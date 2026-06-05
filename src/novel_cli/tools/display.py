"""显示块模块。

提供各种显示块类型，用于在工具结果中展示特定格式的信息。
"""

from typing import Literal

from kosong.tooling import DisplayBlock
from pydantic import BaseModel


class DiffDisplayBlock(DisplayBlock):
    """文件差异显示块。

    用于描述文件内容的差异对比。

    Attributes:
        type: 显示块类型，固定为 "diff"。
        path: 文件路径。
        old_text: 原始文本内容。
        new_text: 新文本内容。
        old_start: 原始文本起始行号。
        new_start: 新文本起始行号。
        is_summary: 是否为摘要模式。
    """

    type: str = "diff"
    path: str
    old_text: str
    new_text: str
    old_start: int = 1
    new_start: int = 1
    is_summary: bool = False


class TodoDisplayItem(BaseModel):
    """待办事项条目。

    Attributes:
        title: 待办事项标题。
        status: 状态，可选值为 "pending"、"in_progress" 或 "done"。
    """

    title: str
    status: Literal["pending", "in_progress", "done"]


class TodoDisplayBlock(DisplayBlock):
    """待办列表显示块。

    用于展示待办事项列表的更新。

    Attributes:
        type: 显示块类型，固定为 "todo"。
        items: 待办事项列表。
    """

    type: str = "todo"
    items: list[TodoDisplayItem]


class ShellDisplayBlock(DisplayBlock):
    """Shell 命令显示块。

    用于描述执行的 shell 命令。

    Attributes:
        type: 显示块类型，固定为 "shell"。
        language: 命令语言/解释器。
        command: 命令内容。
    """

    type: str = "shell"
    language: str
    command: str


class BackgroundTaskDisplayBlock(DisplayBlock):
    """后台任务显示块。

    用于描述后台任务的执行状态。

    Attributes:
        type: 显示块类型，固定为 "background_task"。
        task_id: 任务标识符。
        kind: 任务类型。
        status: 任务状态。
        description: 任务描述。
    """

    type: str = "background_task"
    task_id: str
    kind: str
    status: str
    description: str
