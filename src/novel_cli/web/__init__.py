"""Novel CLI Web 界面模块。

提供 Web 服务器创建和运行功能。
"""

from novel_cli.web.app import create_app, run_web_server

__all__ = ["create_app", "run_web_server"]
