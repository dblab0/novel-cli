"""ACP MCP 服务器配置转换模块。

本模块提供将 ACP MCP 服务器配置转换为 FastMCP 配置的功能。
"""

from __future__ import annotations

from typing import Any

import acp.schema
from fastmcp.mcp_config import MCPConfig
from pydantic import ValidationError

from novel_cli.acp.types import MCPServer
from novel_cli.exception import MCPConfigError


def acp_mcp_servers_to_mcp_config(mcp_servers: list[MCPServer]) -> MCPConfig:
    """将 ACP MCP 服务器列表转换为 MCP 配置对象。

    Args:
        mcp_servers: ACP MCP 服务器列表。

    Returns:
        FastMCP 配置对象。

    Raises:
        MCPConfigError: 当 MCP 配置无效时抛出。
    """
    if not mcp_servers:
        return MCPConfig()

    try:
        return MCPConfig.model_validate(
            {"mcpServers": {server.name: _convert_acp_mcp_server(server) for server in mcp_servers}}
        )
    except ValidationError as exc:
        raise MCPConfigError(f"Invalid MCP config from ACP client: {exc}") from exc


def _convert_acp_mcp_server(server: MCPServer) -> dict[str, Any]:
    """将单个 ACP MCP 服务器转换为字典表示。

    Args:
        server: ACP MCP 服务器对象。

    Returns:
        MCP 服务器配置字典。
    """
    match server:
        case acp.schema.HttpMcpServer():
            return {
                "url": server.url,
                "transport": "http",
                "headers": {header.name: header.value for header in server.headers},
            }
        case acp.schema.SseMcpServer():
            return {
                "url": server.url,
                "transport": "sse",
                "headers": {header.name: header.value for header in server.headers},
            }
        case acp.schema.McpServerStdio():
            return {
                "command": server.command,
                "args": server.args,
                "env": {item.name: item.value for item in server.env},
                "transport": "stdio",
            }