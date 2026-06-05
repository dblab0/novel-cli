"""ACP 包的类型别名定义。

本模块定义了 ACP 协议中使用的类型别名，包括 MCP 服务器和 ACP 内容块的联合类型。
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