"""Web UI 命令模块。

提供 Novel CLI web 界面的 CLI 入口。
"""

from pathlib import Path
from typing import Annotated, Literal

import typer

cli = typer.Typer(help="运行 Novel CLI web 界面。")


@cli.callback(invoke_without_command=True)
def web(
    ctx: typer.Context,
    host: Annotated[
        str | None,
        typer.Option("--host", "-h", help="绑定到指定 IP 地址"),
    ] = None,
    network: Annotated[
        bool,
        typer.Option("--network", "-n", help="启用网络访问（绑定到 0.0.0.0）"),
    ] = False,
    port: Annotated[int, typer.Option("--port", "-p", help="绑定的端口")] = 5494,
    reload: Annotated[bool, typer.Option("--reload", help="启用自动重载")] = False,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="自动打开浏览器")
    ] = True,
    auth_token: Annotated[
        str | None,
        typer.Option("--auth-token", help="API 认证的 Bearer token。"),
    ] = None,
    allowed_origins: Annotated[
        str | None,
        typer.Option(
            "--allowed-origins",
            help="允许的 Origin 值列表，以逗号分隔。",
        ),
    ] = None,
    dangerously_omit_auth: Annotated[
        bool,
        typer.Option(
            "--dangerously-omit-auth",
            help="禁用认证检查（在公共网络中使用危险）。",
        ),
    ] = False,
    restrict_sensitive_apis: Annotated[
        bool | None,
        typer.Option(
            "--restrict-sensitive-apis/--no-restrict-sensitive-apis",
            help="禁用敏感 API（配置写入、open-in、文件访问限制）。",
        ),
    ] = None,
    lan_only: Annotated[
        bool,
        typer.Option(
            "--lan-only/--public",
            help="仅允许本地网络访问（默认）或允许公开访问。",
        ),
    ] = True,
    agent: Annotated[
        Literal["default"] | None,
        typer.Option(
            "--agent",
            help="要使用的内置 agent 规格。与 --agent-file 互斥。默认：内置默认 agent。",
        ),
    ] = None,
    agent_file: Annotated[
        Path | None,
        typer.Option(
            "--agent-file",
            exists=True,
            help="自定义 agent 规格文件路径。与 --agent 互斥。",
        ),
    ] = None,
    book: Annotated[
        str | None,
        typer.Option("--book", help="默认书籍名称，所有新会话默认选中此书。"),
    ] = None,
    work_dir: Annotated[
        str | None,
        typer.Option("--work-dir", help="默认工作目录，不存在时报错。"),
    ] = None,
):
    """运行 Novel CLI web 界面。

    Args:
        ctx: Typer 上下文对象。
        host: 绑定的 IP 地址。
        network: 是否启用网络访问。
        port: 绑定的端口。
        reload: 是否启用自动重载。
        open_browser: 是否自动打开浏览器。
        auth_token: API 认证的 Bearer token。
        allowed_origins: 允许的 Origin 值列表。
        dangerously_omit_auth: 是否禁用认证检查。
        restrict_sensitive_apis: 是否禁用敏感 API。
        lan_only: 是否仅允许本地网络访问。
        agent: 内置 agent 名称。
        agent_file: 自定义 agent 规格文件路径。
        book: 默认书籍名称。
        work_dir: 默认工作目录。

    Raises:
        typer.BadParameter: work_dir 路径不存在时抛出。
    """
    from novel_cli.web.app import run_web_server

    # 互斥参数检测
    if agent is not None and agent_file is not None:
        raise typer.BadParameter(
            "--agent 与 --agent-file 不能同时使用",
            param_hint="--agent / --agent-file",
        )

    # 解析 agent 为文件路径
    if agent is not None:
        from novel_cli.agentspec import DEFAULT_AGENT_FILE

        resolved_agent_file = str(DEFAULT_AGENT_FILE)
    else:
        resolved_agent_file = str(agent_file.resolve()) if agent_file else None

    # 校验 work_dir 存在性
    if work_dir is not None:
        work_dir_path = Path(work_dir).expanduser().resolve()
        if not work_dir_path.exists():
            raise typer.BadParameter(
                f"工作目录不存在: {work_dir}",
                param_hint="--work-dir",
            )
        resolved_work_dir = str(work_dir_path)
    else:
        resolved_work_dir = str(Path.cwd().resolve())

    # 确定绑定地址
    if host:
        bind_host = host
    elif network:
        bind_host = "0.0.0.0"
    else:
        bind_host = "127.0.0.1"

    run_web_server(
        host=bind_host,
        port=port,
        reload=reload,
        open_browser=open_browser,
        auth_token=auth_token,
        allowed_origins=allowed_origins,
        dangerously_omit_auth=dangerously_omit_auth,
        restrict_sensitive_apis=restrict_sensitive_apis,
        lan_only=lan_only,
        agent_file=resolved_agent_file,
        book_name=book,
        work_dir=resolved_work_dir,
    )
