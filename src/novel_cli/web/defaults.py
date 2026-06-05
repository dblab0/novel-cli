"""Web 模式 CLI 启动默认参数模型。

定义 WebDefaults 类型安全模型，用于存储 CLI 传入的默认参数，
并通过 app.state.defaults 注入到 API 层。
"""

from __future__ import annotations

from pydantic import BaseModel


class WebDefaults(BaseModel):
    """Web 模式 CLI 启动默认参数。

    Attributes:
        agent_file: 默认 agent 规格文件绝对路径，None 表示使用内置默认 agent。
        book_name: 默认书籍名称，None 表示不指定书籍。
        work_dir: 默认工作目录绝对路径，CLI 层保证永远非 None（D6）。
    """

    agent_file: str | None = None
    book_name: str | None = None
    work_dir: str
