"""API 路由模块。

导出配置、会话和打开路径等 API 路由。
"""

from novel_cli.web.api import config, open_in, sessions

config_router = config.router
sessions_router = sessions.router
work_dirs_router = sessions.work_dirs_router
defaults_router = sessions.defaults_router
books_router = sessions.books_router
open_in_router = open_in.router

__all__ = [
    "books_router",
    "config_router",
    "defaults_router",
    "open_in_router",
    "sessions_router",
    "work_dirs_router",
]
