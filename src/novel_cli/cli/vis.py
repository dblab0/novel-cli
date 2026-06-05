"""可视化命令模块。

提供 Novel Agent Tracing Visualizer 的 CLI 入口。
"""

from typing import Annotated

import typer

cli = typer.Typer(help="运行 Novel Agent Tracing Visualizer。")


@cli.callback(invoke_without_command=True)
def vis(
    ctx: typer.Context,
    host: Annotated[
        str | None,
        typer.Option("--host", "-h", help="绑定到指定 IP 地址"),
    ] = None,
    network: Annotated[
        bool,
        typer.Option("--network", "-n", help="启用网络访问（绑定到 0.0.0.0）"),
    ] = False,
    port: Annotated[int, typer.Option("--port", "-p", help="绑定的端口")] = 5495,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="自动打开浏览器")
    ] = True,
    reload: Annotated[bool, typer.Option("--reload", help="启用自动重载")] = False,
):
    """启动 agent tracing 可视化工具。

    Args:
        ctx: Typer 上下文对象。
        host: 绑定的 IP 地址。
        network: 是否启用网络访问。
        port: 绑定的端口。
        open_browser: 是否自动打开浏览器。
        reload: 是否启用自动重载。
    """
    from novel_cli.vis.app import run_vis_server

    # 确定绑定地址（逻辑与 novel web 相同）
    if host:
        bind_host = host
    elif network:
        bind_host = "0.0.0.0"
    else:
        bind_host = "127.0.0.1"

    run_vis_server(host=bind_host, port=port, open_browser=open_browser, reload=reload)
