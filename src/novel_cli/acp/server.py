"""ACP 服务器实现模块。

本模块实现 ACP 服务器的核心功能，包括客户端连接管理、会话创建、加载和恢复等。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

import acp
from kaos.path import KaosPath

from novel_cli.acp.kaos import ACPKaos
from novel_cli.acp.mcp import acp_mcp_servers_to_mcp_config
from novel_cli.acp.session import ACPSession
from novel_cli.acp.tools import replace_tools
from novel_cli.acp.types import ACPContentBlock, MCPServer
from novel_cli.acp.version import ACPVersionSpec, negotiate_version
from novel_cli.app import NovelCLI
from novel_cli.config import Config, LLMModel, load_config, save_config
from novel_cli.constant import NAME, VERSION
from novel_cli.llm import create_llm, derive_model_capabilities
from novel_cli.session import Session
from novel_cli.soul.slash import registry as soul_slash_registry
from novel_cli.soul.toolset import NovelToolset
from novel_cli.utils.logging import logger


class ACPServer:
    """ACP 服务器类。

    管理 ACP 客户端连接、会话生命周期和服务器状态。

    Attributes:
        client_capabilities: ACP 客户端能力。
        conn: ACP 客户端连接。
        sessions: 会话 ID 到会话和模型转换器的映射。
        negotiated_version: 协商后的协议版本。
        _auth_methods: 认证方法列表。
    """

    def __init__(self) -> None:
        """初始化 ACP 服务器。"""
        self.client_capabilities: acp.schema.ClientCapabilities | None = None
        self.conn: acp.Client | None = None
        self.sessions: dict[str, tuple[ACPSession, _ModelIDConv]] = {}
        self.negotiated_version: ACPVersionSpec | None = None
        self._auth_methods: list[acp.schema.AuthMethod] = []

    def on_connect(self, conn: acp.Client) -> None:
        """处理客户端连接。

        Args:
            conn: ACP 客户端连接。
        """
        logger.info("ACP client connected")
        self.conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: acp.schema.ClientCapabilities | None = None,
        client_info: acp.schema.Implementation | None = None,
        **kwargs: Any,
    ) -> acp.InitializeResponse:
        """初始化 ACP 服务器。

        协商协议版本，设置客户端能力，并构建认证方法列表。

        Args:
            protocol_version: 客户端协议版本。
            client_capabilities: 客户端能力（可选）。
            client_info: 客户端实现信息（可选）。
            **kwargs: 其他参数。

        Returns:
            ACP 初始化响应对象。
        """
        self.negotiated_version = negotiate_version(protocol_version)
        logger.info(
            "ACP server initialized with client protocol version: {version}, "
            "negotiated version: {negotiated}, "
            "client capabilities: {capabilities}, client info: {info}",
            version=protocol_version,
            negotiated=self.negotiated_version,
            capabilities=client_capabilities,
            info=client_info,
        )
        self.client_capabilities = client_capabilities

        # 获取当前进程的命令和参数，用于终端认证
        command = sys.argv[0]
        args: list[str] = []

        # 为错误响应构建终端认证数据
        terminal_args = args + ["setup"]

        # 构建并缓存认证方法，用于 AUTH_REQUIRED 错误
        self._auth_methods = [
            acp.schema.AuthMethod(
                id="setup",
                name="Configure a provider",
                description=(
                    "Run `novel setup` command in the terminal, "
                    "then follow the instructions to configure a provider and model."
                ),
                # 将认证数据存储在 field_meta 中，用于构建 AUTH_REQUIRED 错误
                field_meta={
                    "terminal-auth": {
                        "command": command,
                        "args": terminal_args,
                        "label": "Novel Code Login",
                        "env": {},
                        "type": "terminal",
                    }
                },
            ),
        ]

        return acp.InitializeResponse(
            protocol_version=self.negotiated_version.protocol_version,
            agent_capabilities=acp.schema.AgentCapabilities(
                load_session=True,
                prompt_capabilities=acp.schema.PromptCapabilities(
                    embedded_context=True, image=True, audio=False
                ),
                mcp_capabilities=acp.schema.McpCapabilities(http=True, sse=False),
                session_capabilities=acp.schema.SessionCapabilities(
                    list=acp.schema.SessionListCapabilities(),
                    resume=acp.schema.SessionResumeCapabilities(),
                ),
            ),
            auth_methods=self._auth_methods,
            agent_info=acp.schema.Implementation(name=NAME, version=VERSION),
        )

    @staticmethod
    def _check_provider_usable(config: Config) -> str | None:
        """检查提供者是否可用。

        如果配置了带有 API key 的提供者，返回 None；否则返回原因字符串。

        Args:
            config: 配置对象。

        Returns:
            None 表示可用，否则返回不可用的原因字符串。
        """
        if not config.default_model or config.default_model not in config.models:
            return "no default model configured"
        model = config.models[config.default_model]
        if model.provider not in config.providers:
            return "provider not found for default model"
        provider = config.providers[model.provider]
        if not provider.api_key.get_secret_value():
            return "no API key configured for provider"
        return None

    def _check_auth(self) -> None:
        """检查是否配置了带有 API key 的提供者。

        如果没有配置，抛出 AUTH_REQUIRED 错误。

        Raises:
            acp.RequestError: 当认证失败时抛出 AUTH_REQUIRED 错误。
        """
        config = load_config()
        reason = self._check_provider_usable(config)
        if reason:
            auth_methods_data: list[dict[str, Any]] = []
            for m in self._auth_methods:
                if m.field_meta and "terminal-auth" in m.field_meta:
                    terminal_auth = m.field_meta["terminal-auth"]
                    auth_methods_data.append(
                        {
                            "id": m.id,
                            "name": m.name,
                            "description": m.description,
                            "type": terminal_auth.get("type", "terminal"),
                            "args": terminal_auth.get("args", []),
                            "env": terminal_auth.get("env", {}),
                        }
                    )

            logger.warning("Authentication required, {reason}", reason=reason)
            raise acp.RequestError.auth_required({"authMethods": auth_methods_data})

    async def new_session(
        self, cwd: str, mcp_servers: list[MCPServer] | None = None, **kwargs: Any
    ) -> acp.NewSessionResponse:
        """创建新会话。

        Args:
            cwd: 工作目录路径。
            mcp_servers: MCP 服务器列表（可选）。
            **kwargs: 其他参数。

        Returns:
            ACP 新会话响应对象。

        Raises:
            AssertionError: 当 ACP 客户端未连接或未初始化时抛出。
        """
        logger.info("Creating new session for working directory: {cwd}", cwd=cwd)
        assert self.conn is not None, "ACP client not connected"
        assert self.client_capabilities is not None, "ACP connection not initialized"

        # 创建会话前检查认证
        self._check_auth()

        session = await Session.create(KaosPath.unsafe_from_local_path(Path(cwd)))

        mcp_config = acp_mcp_servers_to_mcp_config(mcp_servers or [])
        cli_instance = await NovelCLI.create(
            session,
            mcp_configs=[mcp_config],
        )
        config = cli_instance.soul.runtime.config
        acp_kaos = ACPKaos(self.conn, session.id, self.client_capabilities)
        acp_session = ACPSession(session.id, cli_instance, self.conn, kaos=acp_kaos)
        model_id_conv = _ModelIDConv(config.default_model, config.default_thinking)
        self.sessions[session.id] = (acp_session, model_id_conv)

        if isinstance(cli_instance.soul.agent.toolset, NovelToolset):
            replace_tools(
                self.client_capabilities,
                self.conn,
                session.id,
                cli_instance.soul.agent.toolset,
                cli_instance.soul.runtime,
            )

        available_commands = [
            acp.schema.AvailableCommand(name=cmd.name, description=cmd.description)
            for cmd in soul_slash_registry.list_commands()
        ]
        asyncio.create_task(
            self.conn.session_update(
                session_id=session.id,
                update=acp.schema.AvailableCommandsUpdate(
                    session_update="available_commands_update",
                    available_commands=available_commands,
                ),
            )
        )
        return acp.NewSessionResponse(
            session_id=session.id,
            modes=acp.schema.SessionModeState(
                available_modes=[
                    acp.schema.SessionMode(
                        id="default",
                        name="Default",
                        description="The default mode.",
                    ),
                ],
                current_mode_id="default",
            ),
            models=acp.schema.SessionModelState(
                available_models=_expand_llm_models(config.models),
                current_model_id=model_id_conv.to_acp_model_id(),
            ),
        )

    async def _setup_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[MCPServer] | None = None,
    ) -> tuple[ACPSession, _ModelIDConv]:
        """加载或恢复会话。

        load_session 和 resume_session 的共享实现。

        Args:
            cwd: 工作目录路径。
            session_id: 会话 ID。
            mcp_servers: MCP 服务器列表（可选）。

        Returns:
            ACP 会话和模型转换器的元组。

        Raises:
            acp.RequestError: 当会话未找到时抛出。
            AssertionError: 当 ACP 客户端未连接或未初始化时抛出。
        """
        assert self.conn is not None, "ACP client not connected"
        assert self.client_capabilities is not None, "ACP connection not initialized"

        work_dir = KaosPath.unsafe_from_local_path(Path(cwd))
        session = await Session.find(work_dir, session_id)
        if session is None:
            logger.error(
                "Session not found: {id} for working directory: {cwd}", id=session_id, cwd=cwd
            )
            raise acp.RequestError.invalid_params({"session_id": "Session not found"})

        mcp_config = acp_mcp_servers_to_mcp_config(mcp_servers or [])
        cli_instance = await NovelCLI.create(
            session,
            mcp_configs=[mcp_config],
            resumed=True,  # _setup_session 加载现有会话
        )
        config = cli_instance.soul.runtime.config
        acp_kaos = ACPKaos(self.conn, session.id, self.client_capabilities)
        acp_session = ACPSession(session.id, cli_instance, self.conn, kaos=acp_kaos)
        model_id_conv = _ModelIDConv(config.default_model, config.default_thinking)
        self.sessions[session.id] = (acp_session, model_id_conv)

        if isinstance(cli_instance.soul.agent.toolset, NovelToolset):
            replace_tools(
                self.client_capabilities,
                self.conn,
                session.id,
                cli_instance.soul.agent.toolset,
                cli_instance.soul.runtime,
            )

        return acp_session, model_id_conv

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: list[MCPServer] | None = None, **kwargs: Any
    ) -> None:
        """加载现有会话。

        Args:
            cwd: 工作目录路径。
            session_id: 会话 ID。
            mcp_servers: MCP 服务器列表（可选）。
            **kwargs: 其他参数。
        """
        logger.info("Loading session: {id} for working directory: {cwd}", id=session_id, cwd=cwd)

        if session_id in self.sessions:
            logger.warning("Session already loaded: {id}", id=session_id)
            return

        # 加载会话前检查认证
        self._check_auth()

        await self._setup_session(cwd, session_id, mcp_servers)
        # TODO: 回放会话历史？

    async def resume_session(
        self, cwd: str, session_id: str, mcp_servers: list[MCPServer] | None = None, **kwargs: Any
    ) -> acp.schema.ResumeSessionResponse:
        """恢复会话。

        Args:
            cwd: 工作目录路径。
            session_id: 会话 ID。
            mcp_servers: MCP 服务器列表（可选）。
            **kwargs: 其他参数。

        Returns:
            ACP 恢复会话响应对象。
        """
        logger.info("Resuming session: {id} for working directory: {cwd}", id=session_id, cwd=cwd)

        if session_id not in self.sessions:
            await self._setup_session(cwd, session_id, mcp_servers)

        acp_session, model_id_conv = self.sessions[session_id]
        config = acp_session.cli.soul.runtime.config
        return acp.schema.ResumeSessionResponse(
            modes=acp.schema.SessionModeState(
                available_modes=[
                    acp.schema.SessionMode(
                        id="default",
                        name="Default",
                        description="The default mode.",
                    ),
                ],
                current_mode_id="default",
            ),
            models=acp.schema.SessionModelState(
                available_models=_expand_llm_models(config.models),
                current_model_id=model_id_conv.to_acp_model_id(),
            ),
        )

    async def fork_session(
        self, cwd: str, session_id: str, mcp_servers: list[MCPServer] | None = None, **kwargs: Any
    ) -> acp.schema.ForkSessionResponse:
        """分支会话。

        Args:
            cwd: 工作目录路径。
            session_id: 会话 ID。
            mcp_servers: MCP 服务器列表（可选）。
            **kwargs: 其他参数。

        Returns:
            ACP 分支会话响应对象。

        Raises:
            NotImplementedError: 当前未实现。
        """
        raise NotImplementedError

    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any
    ) -> acp.schema.ListSessionsResponse:
        """列出会话。

        Args:
            cursor: 分页游标（可选）。
            cwd: 工作目录路径（可选）。
            **kwargs: 其他参数。

        Returns:
            ACP 会话列表响应对象。
        """
        logger.info("Listing sessions for working directory: {cwd}", cwd=cwd)
        if cwd is None:
            return acp.schema.ListSessionsResponse(sessions=[], next_cursor=None)
        work_dir = KaosPath.unsafe_from_local_path(Path(cwd))
        sessions = await Session.list(work_dir)
        return acp.schema.ListSessionsResponse(
            sessions=[
                acp.schema.SessionInfo(
                    cwd=cwd,
                    session_id=s.id,
                    title=s.title,
                    updated_at=datetime.fromtimestamp(s.updated_at).astimezone().isoformat(),
                )
                for s in sessions
            ],
            next_cursor=None,
        )

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> None:
        """设置会话模式。

        Args:
            mode_id: 模式 ID。
            session_id: 会话 ID。
            **kwargs: 其他参数。

        Raises:
            AssertionError: 当模式 ID 不是 "default" 时抛出。
        """
        assert mode_id == "default", "Only default mode is supported"

    async def set_session_model(self, model_id: str, session_id: str, **kwargs: Any) -> None:
        """设置会话模型。

        Args:
            model_id: 模型 ID。
            session_id: 会话 ID。
            **kwargs: 其他参数。

        Raises:
            acp.RequestError: 当会话或模型未找到时抛出。
        """
        logger.info(
            "Setting session model to {model_id} for session: {id}",
            model_id=model_id,
            id=session_id,
        )
        if session_id not in self.sessions:
            logger.error("Session not found: {id}", id=session_id)
            raise acp.RequestError.invalid_params({"session_id": "Session not found"})

        acp_session, current_model_id = self.sessions[session_id]
        cli_instance = acp_session.cli
        model_id_conv = _ModelIDConv.from_acp_model_id(model_id)
        if model_id_conv == current_model_id:
            return

        config = cli_instance.soul.runtime.config
        new_model = config.models.get(model_id_conv.model_key)
        if new_model is None:
            logger.error("Model not found: {model_key}", model_key=model_id_conv.model_key)
            raise acp.RequestError.invalid_params({"model_id": "Model not found"})
        new_provider = config.providers.get(new_model.provider)
        if new_provider is None:
            logger.error(
                "Provider not found: {provider} for model: {model_key}",
                provider=new_model.provider,
                model_key=model_id_conv.model_key,
            )
            raise acp.RequestError.invalid_params({"model_id": "Model's provider not found"})

        new_llm = create_llm(
            new_provider,
            new_model,
            session_id=acp_session.id,
            thinking=model_id_conv.thinking,
        )
        cli_instance.soul.runtime.llm = new_llm

        config.default_model = model_id_conv.model_key
        config.default_thinking = model_id_conv.thinking
        assert config.is_from_default_location, "`novel acp` must use the default config location"
        config_for_save = load_config()
        config_for_save.default_model = model_id_conv.model_key
        config_for_save.default_thinking = model_id_conv.thinking
        save_config(config_for_save)

    async def authenticate(self, method_id: str, **kwargs: Any) -> acp.AuthenticateResponse | None:
        """认证处理。

        对于终端认证，此方法通常不会直接调用（用户在终端中完成认证）。
        为完整性而实现。

        Args:
            method_id: 认证方法 ID。
            **kwargs: 其他参数。

        Returns:
            认证成功时返回认证响应对象，失败时抛出错误。

        Raises:
            acp.RequestError: 当认证失败或认证方法未知时抛出。
        """
        if method_id == "setup":
            config = load_config()
            reason = self._check_provider_usable(config)
            if reason is None:
                logger.info("Authentication successful for method: {id}", id=method_id)
                return acp.AuthenticateResponse()
            else:
                logger.warning(
                    "Authentication not complete for method: {id} ({reason})",
                    id=method_id,
                    reason=reason,
                )
                raise acp.RequestError.auth_required(
                    {
                        "message": "Please configure a provider in terminal first",
                        "authMethods": self._auth_methods,
                    }
                )

        logger.error("Unknown auth method: {method_id}", method_id=method_id)
        raise acp.RequestError.invalid_params({"method_id": "Unknown auth method"})

    async def prompt(
        self, prompt: list[ACPContentBlock], session_id: str, **kwargs: Any
    ) -> acp.PromptResponse:
        """处理用户提示请求。

        Args:
            prompt: ACP 内容块列表。
            session_id: 会话 ID。
            **kwargs: 其他参数。

        Returns:
            ACP 提示响应对象。

        Raises:
            acp.RequestError: 当会话未找到时抛出。
        """
        logger.info("Received prompt request for session: {id}", id=session_id)
        if session_id not in self.sessions:
            logger.error("Session not found: {id}", id=session_id)
            raise acp.RequestError.invalid_params({"session_id": "Session not found"})
        acp_session, *_ = self.sessions[session_id]
        return await acp_session.prompt(prompt)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """取消正在运行的提示请求。

        Args:
            session_id: 会话 ID。
            **kwargs: 其他参数。

        Raises:
            acp.RequestError: 当会话未找到时抛出。
        """
        logger.info("Received cancel request for session: {id}", id=session_id)
        if session_id not in self.sessions:
            logger.error("Session not found: {id}", id=session_id)
            raise acp.RequestError.invalid_params({"session_id": "Session not found"})
        acp_session, *_ = self.sessions[session_id]
        await acp_session.cancel()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """处理扩展方法。

        Args:
            method: 方法名称。
            params: 方法参数。

        Returns:
            方法返回值。

        Raises:
            NotImplementedError: 当前未实现。
        """
        raise NotImplementedError

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """处理扩展通知。

        Args:
            method: 通知名称。
            params: 通知参数。

        Raises:
            NotImplementedError: 当前未实现。
        """
        raise NotImplementedError


class _ModelIDConv(NamedTuple):
    """模型 ID 转换器。

    用于在内部模型键和 ACP 模型 ID 之间转换。

    Attributes:
        model_key: 内部模型键。
        thinking: 是否启用思考模式。
    """

    model_key: str
    thinking: bool

    @classmethod
    def from_acp_model_id(cls, model_id: str) -> _ModelIDConv:
        """从 ACP 模型 ID 创建转换器。

        Args:
            model_id: ACP 模型 ID（可能包含 ",thinking" 后缀）。

        Returns:
            模型 ID 转换器对象。
        """
        if model_id.endswith(",thinking"):
            return _ModelIDConv(model_id[: -len(",thinking")], True)
        return _ModelIDConv(model_id, False)

    def to_acp_model_id(self) -> str:
        """转换为 ACP 模型 ID。

        Returns:
            ACP 模型 ID（如果启用思考模式则包含 ",thinking" 后缀）。
        """
        if self.thinking:
            return f"{self.model_key},thinking"
        return self.model_key


def _expand_llm_models(models: dict[str, LLMModel]) -> list[acp.schema.ModelInfo]:
    """展开 LLM 模型列表。

    为支持思考功能的模型添加思考变体。

    Args:
        models: 模型键到模型配置的映射。

    Returns:
        展开后的模型信息列表。
    """
    expanded_models: list[acp.schema.ModelInfo] = []
    for model_key, model in models.items():
        capabilities = derive_model_capabilities(model)
        if "thinking" in model.model or "reason" in model.model:
            # 始终思考的模型
            expanded_models.append(
                acp.schema.ModelInfo(
                    model_id=_ModelIDConv(model_key, True).to_acp_model_id(),
                    name=f"{model.model}",
                )
            )
        else:
            expanded_models.append(
                acp.schema.ModelInfo(
                    model_id=model_key,
                    name=model.model,
                )
            )
            if "thinking" in capabilities:
                # 添加思考变体
                expanded_models.append(
                    acp.schema.ModelInfo(
                        model_id=_ModelIDConv(model_key, True).to_acp_model_id(),
                        name=f"{model.model} (thinking)",
                    )
                )
    return expanded_models