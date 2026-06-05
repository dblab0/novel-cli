"""ACP 包的入口模块。

本模块提供 ACP 包的公共接口，导出常用的类型别名供外部模块使用。
"""

from __future__ import annotations

import acp

# MCP 服务器类型别名，包含 HTTP、SSE 和 Stdio 三种传输方式
MCPServer = acp.schema.HttpMcpServer | acp.schema.SseMcpServer | acp.schema.McpServerStdio

# ACP 内容块类型别名，包含文本、图片、音频、资源和嵌入资源五种类型
ACPContentBlock = (
    acp.schema.TextContentBlock
    | acp.schema.ImageContentBlock
    | acp.schema.AudioContentBlock
    | acp.schema.ResourceContentBlock
    | acp.schema.EmbeddedResourceContentBlock
)


def acp_main() -> None:
    """ACP 多会话服务器入口点。"""
    import asyncio

    from novel_cli.acp.server import ACPServer
    from novel_cli.app import enable_logging
    from novel_cli.utils.logging import logger

    enable_logging()
    logger.info("Starting ACP server on stdio")
    asyncio.run(acp.run_agent(ACPServer(), use_unstable_protocol=True))