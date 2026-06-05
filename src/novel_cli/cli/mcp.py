"""MCP 服务器管理命令模块。

提供 MCP 服务器配置的添加、移除、列表、认证和测试功能。
"""

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

cli = typer.Typer(help="管理 MCP 服务器配置。")


def get_global_mcp_config_file() -> Path:
    """获取全局 MCP 配置文件路径。

    Returns:
        全局 MCP 配置文件路径。
    """
    from novel_cli.share import get_share_dir

    return get_share_dir() / "mcp.json"


def _load_mcp_config() -> dict[str, Any]:
    """从全局 MCP 配置文件加载配置。

    Returns:
        MCP 配置字典。

    Raises:
        typer.BadParameter: 当配置文件 JSON 无效或配置格式无效时。
    """
    from fastmcp.mcp_config import MCPConfig
    from pydantic import ValidationError

    mcp_file = get_global_mcp_config_file()
    if not mcp_file.exists():
        return {"mcpServers": {}}
    try:
        config = json.loads(mcp_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"Invalid JSON in MCP config file '{mcp_file}': {e}") from e

    try:
        MCPConfig.model_validate(config)
    except ValidationError as e:
        raise typer.BadParameter(f"Invalid MCP config in '{mcp_file}': {e}") from e

    return config


def _save_mcp_config(config: dict[str, Any]) -> None:
    """保存 MCP 配置到默认文件。

    Args:
        config: MCP 配置字典。
    """
    mcp_file = get_global_mcp_config_file()
    mcp_file.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_mcp_server(name: str, *, require_remote: bool = False) -> dict[str, Any]:
    """通过名称获取 MCP 服务器配置。

    Args:
        name: MCP 服务器名称。
        require_remote: 是否要求服务器为远程服务器。

    Returns:
        MCP 服务器配置字典。

    Raises:
        typer.Exit: 当服务器未找到或不满足远程要求时。
    """
    config = _load_mcp_config()
    servers = config.get("mcpServers", {})
    if name not in servers:
        typer.echo(f"MCP server '{name}' not found.", err=True)
        raise typer.Exit(code=1)
    server = servers[name]
    if require_remote and "url" not in server:
        typer.echo(f"MCP server '{name}' is not a remote server.", err=True)
        raise typer.Exit(code=1)
    return server


def _parse_key_value_pairs(
    items: list[str], option_name: str, *, separator: str = "=", strip_whitespace: bool = False
) -> dict[str, str]:
    """从 CLI 选项解析键/值对。

    Args:
        items: 键/值对字符串列表。
        option_name: 选项名称（用于错误提示）。
        separator: 分隔符字符。
        strip_whitespace: 是否去除键和值的空白字符。

    Returns:
        解析后的键/值对字典。

    Raises:
        typer.Exit: 当格式无效或键为空时。
    """
    parsed: dict[str, str] = {}
    for item in items:
        if separator not in item:
            typer.echo(
                f"Invalid {option_name} format: {item} (expected KEY{separator}VALUE).",
                err=True,
            )
            raise typer.Exit(code=1)
        key, value = item.split(separator, 1)
        if strip_whitespace:
            key, value = key.strip(), value.strip()
        if not key:
            typer.echo(f"Invalid {option_name} format: {item} (empty key).", err=True)
            raise typer.Exit(code=1)
        parsed[key] = value
    return parsed


Transport = Literal["stdio", "http"]


@cli.command(
    "add",
    epilog="""
    Examples:\n
      \n
      # Add streamable HTTP server:\n
      novel mcp add --transport http context7 https://mcp.context7.com/mcp --header \"CONTEXT7_API_KEY: ctx7sk-your-key\"\n
      \n
      # Add streamable HTTP server with OAuth authorization:\n
      novel mcp add --transport http --auth oauth linear https://mcp.linear.app/mcp\n
      \n
      # Add stdio server:\n
      novel mcp add --transport stdio chrome-devtools -- npx chrome-devtools-mcp@latest
    """.strip(),  # noqa: E501
)
def mcp_add(
    name: Annotated[
        str,
        typer.Argument(help="要添加的 MCP 服务器名称。"),
    ],
    server_args: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="TARGET_OR_COMMAND...",
            help="对于 http：服务器 URL。对于 stdio：要运行的命令（以 `--` 为前缀）。",
        ),
    ] = None,
    transport: Annotated[
        Transport,
        typer.Option(
            "--transport",
            "-t",
            help="MCP 服务器的传输类型。默认：stdio。",
        ),
    ] = "stdio",
    env: Annotated[
        list[str] | None,
        typer.Option(
            "--env",
            "-e",
            help="环境变量，格式为 KEY=VALUE。可多次指定。",
        ),
    ] = None,
    header: Annotated[
        list[str] | None,
        typer.Option(
            "--header",
            "-H",
            help="HTTP 头，格式为 KEY:VALUE。可多次指定。",
        ),
    ] = None,
    auth: Annotated[
        str | None,
        typer.Option(
            "--auth",
            "-a",
            help="认证类型（如 'oauth'）。",
        ),
    ] = None,
):
    """添加 MCP 服务器。

    Args:
        name: MCP 服务器名称。
        server_args: 服务器参数（URL 或命令）。
        transport: 传输类型。
        env: 环境变量列表。
        header: HTTP 头列表。
        auth: 认证类型。

    Raises:
        typer.Exit: 当参数无效或添加失败时。
    """
    config = _load_mcp_config()
    server_args = server_args or []

    if transport not in {"stdio", "http"}:
        typer.echo(f"Unsupported transport: {transport}.", err=True)
        raise typer.Exit(code=1)

    if transport == "stdio":
        if not server_args:
            typer.echo(
                "For stdio transport, provide the command to start the MCP server after `--`.",
                err=True,
            )
            raise typer.Exit(code=1)
        if header:
            typer.echo("--header is only valid for http transport.", err=True)
            raise typer.Exit(code=1)
        if auth:
            typer.echo("--auth is only valid for http transport.", err=True)
            raise typer.Exit(code=1)
        command, *command_args = server_args
        server_config: dict[str, Any] = {"command": command, "args": command_args}
        if env:
            server_config["env"] = _parse_key_value_pairs(env, "env")
    else:
        if env:
            typer.echo("--env is only supported for stdio transport.", err=True)
            raise typer.Exit(code=1)
        if not server_args:
            typer.echo("URL is required for http transport.", err=True)
            raise typer.Exit(code=1)
        if len(server_args) > 1:
            typer.echo(
                "Multiple targets provided. Supply a single URL for http transport.",
                err=True,
            )
            raise typer.Exit(code=1)
        server_config = {"url": server_args[0], "transport": "http"}
        if header:
            server_config["headers"] = _parse_key_value_pairs(
                header, "header", separator=":", strip_whitespace=True
            )
        if auth:
            server_config["auth"] = auth

    if "mcpServers" not in config:
        config["mcpServers"] = {}
    config["mcpServers"][name] = server_config
    _save_mcp_config(config)
    typer.echo(f"Added MCP server '{name}' to {get_global_mcp_config_file()}.")


@cli.command("remove")
def mcp_remove(
    name: Annotated[
        str,
        typer.Argument(help="要移除的 MCP 服务器名称。"),
    ],
):
    """移除 MCP 服务器。

    Args:
        name: MCP 服务器名称。

    Raises:
        typer.Exit: 当服务器未找到时。
    """
    _get_mcp_server(name)
    config = _load_mcp_config()
    del config["mcpServers"][name]
    _save_mcp_config(config)
    typer.echo(f"Removed MCP server '{name}' from {get_global_mcp_config_file()}.")


def _has_oauth_tokens(server_url: str) -> bool:
    """检查服务器是否存在 OAuth 令牌。

    Args:
        server_url: 服务器 URL。

    Returns:
        是否存在有效的 OAuth 令牌。
    """
    import asyncio

    async def _check() -> bool:
        try:
            from fastmcp.client.auth.oauth import FileTokenStorage

            storage = FileTokenStorage(server_url=server_url)
            tokens = await storage.get_tokens()
            return tokens is not None
        except Exception:
            return False

    return asyncio.run(_check())


@cli.command("list")
def mcp_list():
    """列出所有 MCP 服务器。"""
    config_file = get_global_mcp_config_file()
    config = _load_mcp_config()
    servers: dict[str, Any] = config.get("mcpServers", {})

    typer.echo(f"MCP config file: {config_file}")
    if not servers:
        typer.echo("No MCP servers configured.")
        return

    for name, server in servers.items():
        if "command" in server:
            cmd = server["command"]
            cmd_args = " ".join(server.get("args", []))
            line = f"{name} (stdio): {cmd} {cmd_args}".rstrip()
        elif "url" in server:
            transport = server.get("transport") or "http"
            if transport == "streamable-http":
                transport = "http"
            line = f"{name} ({transport}): {server['url']}"
            if server.get("auth") == "oauth" and not _has_oauth_tokens(server["url"]):
                line += " [authorization required - run: novel mcp auth " + name + "]"
        else:
            line = f"{name}: {server}"
        typer.echo(f"  {line}")


@cli.command("auth")
def mcp_auth(
    name: Annotated[
        str,
        typer.Argument(help="要授权的 MCP 服务器名称。"),
    ],
):
    """与 OAuth 启用的 MCP 服务器进行授权。

    Args:
        name: MCP 服务器名称。

    Raises:
        typer.Exit: 当服务器未使用 OAuth 或授权失败时。
    """
    import asyncio

    server = _get_mcp_server(name, require_remote=True)
    if server.get("auth") != "oauth":
        typer.echo(f"MCP server '{name}' does not use OAuth. Add with --auth oauth.", err=True)
        raise typer.Exit(code=1)

    async def _auth() -> None:
        import fastmcp

        typer.echo(f"Authorizing with '{name}'...")
        typer.echo("A browser window will open for authorization.")

        client = fastmcp.Client({"mcpServers": {name: server}})
        try:
            async with client:
                tools = await client.list_tools()
                typer.echo(f"Successfully authorized with '{name}'.")
                typer.echo(f"Available tools: {len(tools)}")
        except Exception as e:
            typer.echo(f"Authorization failed: {type(e).__name__}: {e}", err=True)
            raise typer.Exit(code=1) from None

    asyncio.run(_auth())


@cli.command("reset-auth")
def mcp_reset_auth(
    name: Annotated[
        str,
        typer.Argument(help="要重置授权的 MCP 服务器名称。"),
    ],
):
    """重置 MCP 服务器的 OAuth 授权（清除缓存的令牌）。

    Args:
        name: MCP 服务器名称。

    Raises:
        typer.Exit: 当 OAuth 支持不可用或清除失败时。
    """
    server = _get_mcp_server(name, require_remote=True)

    try:
        from fastmcp.client.auth.oauth import FileTokenStorage

        storage = FileTokenStorage(server_url=server["url"])
        storage.clear()
        typer.echo(f"OAuth tokens cleared for '{name}'.")
    except ImportError:
        typer.echo("OAuth support not available.", err=True)
        raise typer.Exit(code=1) from None
    except Exception as e:
        typer.echo(f"Failed to clear tokens: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from None


@cli.command("test")
def mcp_test(
    name: Annotated[
        str,
        typer.Argument(help="要测试的 MCP 服务器名称。"),
    ],
):
    """测试 MCP 服务器连接并列出可用工具。

    Args:
        name: MCP 服务器名称。

    Raises:
        typer.Exit: 当连接失败时。
    """
    import asyncio

    server = _get_mcp_server(name)

    async def _test() -> None:
        import fastmcp

        typer.echo(f"Testing connection to '{name}'...")
        client = fastmcp.Client({"mcpServers": {name: server}})

        try:
            async with client:
                tools = await client.list_tools()
                typer.echo(f"✓ Connected to '{name}'")
                typer.echo(f"  Available tools: {len(tools)}")
                if tools:
                    typer.echo("  Tools:")
                    for tool in tools:
                        desc = tool.description or ""
                        if len(desc) > 50:
                            desc = desc[:47] + "..."
                        typer.echo(f"    - {tool.name}: {desc}")
        except Exception as e:
            typer.echo(f"✗ Connection failed: {type(e).__name__}: {e}", err=True)
            raise typer.Exit(code=1) from None

    asyncio.run(_test())
