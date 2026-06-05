"""ACP 服务模块。

本模块提供 ACP（Agent Communication Protocol）服务器实现，
用于通过 stdio 与 ACP 客户端进行通信。

主要组件：
    ACPServerSingleSession：单会话 ACP 服务器（已弃用）。
    ACP：ACP 服务器封装类。
"""

from __future__ import annotations

from typing import Any, NoReturn

import acp

from novel_cli.acp.types import ACPContentBlock, MCPServer
from novel_cli.soul import Soul
from novel_cli.utils.logging import logger

_DEPRECATED_MESSAGE = (
    "`novel --acp` is deprecated. "
    "Update your ACP client settings to use `novel acp` without any flags or options."
)


class ACPServerSingleSession:
    """单会话 ACP 服务器（已弃用）。

    该类实现了一个仅返回错误的 ACP 服务器，
    用于提示用户迁移到新的命令行接口。

    Attributes:
        soul: 要运行的 Soul 实例。
    """

    def __init__(self, soul: Soul):
        """初始化单会话 ACP 服务器。

        Args:
            soul: 要运行的 Soul 实例。
        """
        self.soul = soul

    def on_connect(self, conn: acp.Client) -> None:
        """客户端连接回调。

        Args:
            conn: ACP 客户端连接。
        """
        logger.info("ACP client connected")

    def _raise(self) -> NoReturn:
        """抛出弃用错误。

        Raises:
            RequestError: 始终抛出，提示用户使用新的命令行接口。
        """
        logger.error(_DEPRECATED_MESSAGE)
        raise acp.RequestError.invalid_params({"error": _DEPRECATED_MESSAGE})

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: acp.schema.ClientCapabilities | None = None,
        client_info: acp.schema.Implementation | None = None,
        **kwargs: Any,
    ) -> acp.InitializeResponse:
        """初始化 ACP 连接（已弃用）。"""
        self._raise()

    async def new_session(
        self, cwd: str, mcp_servers: list[MCPServer] | None = None, **kwargs: Any
    ) -> acp.NewSessionResponse:
        """创建新会话（已弃用）。"""
        self._raise()

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: list[MCPServer] | None = None, **kwargs: Any
    ) -> None:
        """加载现有会话（已弃用）。"""
        self._raise()

    async def resume_session(
        self, cwd: str, session_id: str, mcp_servers: list[MCPServer] | None = None, **kwargs: Any
    ) -> acp.schema.ResumeSessionResponse:
        """恢复会话（已弃用）。"""
        self._raise()

    async def fork_session(
        self, cwd: str, session_id: str, mcp_servers: list[MCPServer] | None = None, **kwargs: Any
    ) -> acp.schema.ForkSessionResponse:
        """分叉会话（已弃用）。"""
        self._raise()

    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any
    ) -> acp.schema.ListSessionsResponse:
        """列出会话（已弃用）。"""
        self._raise()

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> acp.SetSessionModeResponse | None:
        """设置会话模式（已弃用）。"""
        self._raise()

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> acp.SetSessionModelResponse | None:
        """设置会话模型（已弃用）。"""
        self._raise()

    async def authenticate(self, method_id: str, **kwargs: Any) -> acp.AuthenticateResponse | None:
        """认证（已弃用）。"""
        self._raise()

    async def prompt(
        self, prompt: list[ACPContentBlock], session_id: str, **kwargs: Any
    ) -> acp.PromptResponse:
        """发送提示（已弃用）。"""
        self._raise()

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """取消会话（已弃用）。"""
        self._raise()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """扩展方法调用（已弃用）。"""
        self._raise()

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """扩展通知（已弃用）。"""
        self._raise()


class ACP:
    """ACP 服务器封装类。

    使用官方 acp 库运行 ACP 服务器。

    Attributes:
        soul: 要运行的 Soul 实例。
    """

    def __init__(self, soul: Soul):
        """初始化 ACP 服务器。

        Args:
            soul: 要运行的 Soul 实例。
        """
        self.soul = soul

    async def run(self):
        """运行 ACP 服务器。

        在 stdio 上启动单会话 ACP 服务器。
        """
        logger.info("Starting ACP server (single session) on stdio")
        await acp.run_agent(ACPServerSingleSession(self.soul))
