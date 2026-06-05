"""工具集管理模块。

本模块提供智能体工具集的管理功能，包括工具注册、调用、MCP 服务器工具加载等。
支持动态加载工具、外部工具注册、MCP 协议工具集成等特性。

主要类:
    NovelToolset: 核心工具集类，管理工具的注册与调用。
    MCPServerInfo: MCP 服务器信息数据类。
    MCPTool: MCP 协议工具包装类。
    WireExternalTool: 外部工具通信包装类。

关键函数:
    get_current_tool_call_or_none: 获取当前工具调用上下文。
    convert_mcp_tool_result: 转换 MCP 工具结果格式。
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import importlib
import inspect
import json
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, overload

from kosong.tooling import (
    CallableTool,
    CallableTool2,
    HandleResult,
    Tool,
    ToolError,
    ToolOk,
    Toolset,
)
from kosong.tooling.error import (
    ToolNotFoundError,
    ToolRuntimeError,
)
from kosong.tooling.mcp import convert_mcp_content
from kosong.utils.typing import JsonType

from novel_cli import logger
from novel_cli.exception import InvalidToolError, MCPRuntimeError


class ToolCallLoopDetected(Exception):
    """连续重复调用同一工具（参数完全一致）超过阈值时抛出。

    Attributes:
        tool_name: 触发循环检测的工具名称。
        threshold: 连续重复调用的阈值。
    """

    def __init__(self, tool_name: str, threshold: int):
        self.tool_name = tool_name
        self.threshold = threshold
        super().__init__(
            f"工具 {tool_name} 连续 {threshold} 次以相同参数调用，已阻断"
        )


class LoopDetector:
    """追踪最近工具调用，检测连续重复模式。

    使用 deque(maxlen=threshold) 保留最近 N 条调用记录，
    当所有记录完全相同时判定为循环。

    Attributes:
        _threshold: 连续重复调用的阈值。
        _history: 最近 N 次调用的序列化记录。
    """

    def __init__(self, threshold: int = 5):
        self._threshold = threshold
        self._history: deque[tuple[str, str]] = deque(maxlen=threshold)

    def check(self, tool_name: str, params: dict) -> None:
        """检查当前调用是否构成循环，是则抛出 ToolCallLoopDetected。

        语义：前 threshold-1 次正常通过，第 threshold 次在工具执行前抛异常。

        Args:
            tool_name: 工具名称。
            params: 工具调用参数字典。

        Raises:
            ToolCallLoopDetected: 连续 threshold 次以相同参数调用同一工具时抛出。
        """
        key = (tool_name, json.dumps(params, sort_keys=True))
        self._history.append(key)
        if len(self._history) == self._threshold and len(set(self._history)) == 1:
            raise ToolCallLoopDetected(tool_name, self._threshold)

    def reset(self) -> None:
        """重置调用历史，在每个 turn 开始时调用。"""
        self._history.clear()
from novel_cli.hooks.engine import HookEngine
from novel_cli.tools import SkipThisTool
from novel_cli.agentspec import ToolValidator
from novel_cli.wire.types import (
    ContentPart,
    MCPServerSnapshot,
    MCPStatusSnapshot,
    ToolCall,
    ToolCallRequest,
    ToolResult,
    ToolReturnValue,
)

if TYPE_CHECKING:
    import fastmcp
    import mcp
    from fastmcp.client.client import CallToolResult
    from fastmcp.client.transports import ClientTransport
    from fastmcp.mcp_config import MCPConfig

    from novel_cli.soul.agent import Runtime

current_tool_call = ContextVar[ToolCall | None]("current_tool_call", default=None)
"""当前工具调用的上下文变量。"""

_current_session_id: ContextVar[str] = ContextVar("_current_session_id", default="")
"""当前会话 ID 的上下文变量。"""


def set_session_id(sid: str) -> None:
    """设置当前会话 ID。

    Args:
        sid: 会话 ID 字符串。
    """
    _current_session_id.set(sid)


def _get_session_id() -> str:
    """获取当前会话 ID。

    Returns:
        当前会话 ID 字符串。
    """
    return _current_session_id.get()


def get_current_tool_call_or_none() -> ToolCall | None:
    """获取当前工具调用上下文，若无则返回 None。

    当从工具的 `__call__` 方法中调用时，期望返回非 None 值。

    Returns:
        当前工具调用对象，若无则返回 None。
    """
    return current_tool_call.get()


type ToolType = CallableTool | CallableTool2[Any]
"""工具类型别名，表示可调用工具的类型。"""


if TYPE_CHECKING:

    def type_check(novel_toolset: NovelToolset):
        _: Toolset = novel_toolset


def _remove_param_from_schema(tool: Tool, param_name: str) -> Tool:
    """从工具的 JSON Schema 中移除指定参数。

    Args:
        tool: 原始工具对象。
        param_name: 要移除的参数名称。

    Returns:
        移除指定参数后的新工具对象。
    """
    schema = copy.deepcopy(tool.parameters)
    if schema and "properties" in schema:
        props = {k: v for k, v in schema["properties"].items() if k != param_name}
        schema["properties"] = props
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r != param_name]
    return Tool(name=tool.name, description=tool.description, parameters=schema)


class NovelToolset:
    """智能体工具集管理类。

    提供工具的注册、隐藏、查找、调用等功能，同时支持 MCP 服务器工具的加载与管理。

    Attributes:
        _tool_dict: 工具名称到工具实例的映射字典。
        _hidden_tools: 被隐藏的工具名称集合。
        _mcp_servers: MCP 服务器名称到服务器信息的映射。
        _mcp_loading_task: MCP 工具后台加载任务。
        _deferred_mcp_load: 延迟加载的 MCP 配置和运行时元组。
        _hook_engine: 钩子引擎实例。
    """

    def __init__(
        self,
        *,
        get_current_book: Callable[[], str | None] | None = None,
        tool_validators: list[ToolValidator] | None = None,
        loop_threshold: int = 5,
    ) -> None:
        """初始化工具集实例。

        Args:
            get_current_book: 获取当前书籍名称的回调函数，用于注入 book 参数。
            tool_validators: 工具调用顺序验证规则列表。
            loop_threshold: 连续重复调用检测阈值，默认 5。
        """
        self._tool_dict: dict[str, ToolType] = {}
        self._hidden_tools: set[str] = set()
        self._mcp_servers: dict[str, MCPServerInfo] = {}
        self._mcp_loading_task: asyncio.Task[None] | None = None
        self._deferred_mcp_load: tuple[list[MCPConfig], Runtime] | None = None
        self._hook_engine: HookEngine = HookEngine()
        self._get_current_book = get_current_book
        self._tool_validators: list[ToolValidator] = tool_validators or []
        self._overview_queried: set[str] = set()
        self._loop_detector = LoopDetector(threshold=loop_threshold)

    def set_hook_engine(self, engine: HookEngine) -> None:
        """设置钩子引擎实例。

        Args:
            engine: 钩子引擎实例。
        """
        self._hook_engine = engine

    def add(self, tool: ToolType) -> None:
        """添加工具到工具集。

        Args:
            tool: 要添加的工具实例。
        """
        self._tool_dict[tool.name] = tool

    def hide(self, tool_name: str) -> bool:
        """隐藏指定工具，使其对 LLM 不可见。

        Args:
            tool_name: 要隐藏的工具名称。

        Returns:
            若工具存在并成功隐藏返回 True，否则返回 False。
        """
        if tool_name in self._tool_dict:
            self._hidden_tools.add(tool_name)
            return True
        return False

    def unhide(self, tool_name: str) -> None:
        """恢复被隐藏的工具，使其对 LLM 可见。

        Args:
            tool_name: 要恢复显示的工具名称。
        """
        self._hidden_tools.discard(tool_name)

    @overload
    def find(self, tool_name_or_type: str) -> ToolType | None: ...
    @overload
    def find[T: ToolType](self, tool_name_or_type: type[T]) -> T | None: ...
    def find(self, tool_name_or_type: str | type[ToolType]) -> ToolType | None:
        """根据名称或类型查找工具。

        Args:
            tool_name_or_type: 工具名称字符串或工具类型。

        Returns:
            找到的工具实例，若未找到则返回 None。
        """
        if isinstance(tool_name_or_type, str):
            return self._tool_dict.get(tool_name_or_type)
        else:
            for tool in self._tool_dict.values():
                if isinstance(tool, tool_name_or_type):
                    return tool
        return None

    @property
    def tools(self) -> list[Tool]:
        """获取所有可见工具的基础工具对象列表。

        Returns:
        未被隐藏的工具基础对象列表。
        """
        result = []
        for tool in self._tool_dict.values():
            if tool.name in self._hidden_tools:
                continue
            base = tool.base
            # 对 LLM 隐藏小说搜索工具的 book 参数
            if tool.name in ("SearchEntity", "SearchGraph", "SearchCorpus", "ReadChapter"):
                base = _remove_param_from_schema(base, "book")
            result.append(base)
        return result

    def handle(self, tool_call: ToolCall) -> HandleResult:
        """处理工具调用请求。

        Args:
            tool_call: 工具调用请求对象。

        Returns:
        工具调用处理结果，包含异步任务或错误信息。
        """
        token = current_tool_call.set(tool_call)
        try:
            if tool_call.function.name not in self._tool_dict:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    return_value=ToolNotFoundError(tool_call.function.name),
                )

            tool = self._tool_dict[tool_call.function.name]

            try:
                arguments: JsonType = json.loads(tool_call.function.arguments or "{}", strict=False)
            except json.JSONDecodeError as e:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    return_value=ToolError(
                        message=f"参数格式错误，请检查参数是否为合法 JSON: {e}",
                        brief="参数格式错误",
                    ),
                )

            async def _call():
                tool_input_dict = arguments if isinstance(arguments, dict) else {}

                # 注入 book 参数（不覆盖 agent 已有的值）
                if tool_call.function.name in ("SearchEntity", "SearchGraph", "SearchCorpus", "ReadChapter") and self._get_current_book is not None:
                    current_book = self._get_current_book()
                    if current_book:
                        tool_input_dict.setdefault("book", current_book)

                # --- 循环检测（在 book 注入之后、PreToolUse hook 之前）---
                self._loop_detector.check(tool_call.function.name, tool_input_dict)

                # --- PreToolUse 钩子触发 ---
                from novel_cli.hooks import events

                results = await self._hook_engine.trigger(
                    "PreToolUse",
                    matcher_value=tool_call.function.name,
                    input_data=events.pre_tool_use(
                        session_id=_get_session_id(),
                        cwd=str(Path.cwd()),
                        tool_name=tool_call.function.name,
                        tool_input=tool_input_dict,
                        tool_call_id=tool_call.id,
                    ),
                )
                for result in results:
                    if result.action == "block":
                        return ToolResult(
                            tool_call_id=tool_call.id,
                            return_value=ToolError(
                                message=result.reason or "Blocked by PreToolUse hook",
                                brief="Hook blocked",
                            ),
                        )

                # --- tool_validators 检查 ---
                validator_error = self._check_validators(tool_call.function.name, tool_input_dict)
                if validator_error is not None:
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        return_value=validator_error,
                    )

                # --- 执行工具 ---
                try:
                    ret = await tool.call(tool_input_dict)
                except Exception as e:
                    # --- PostToolUseFailure 钩子触发（异步执行，不等待结果）---
                    _hook_task = asyncio.create_task(
                        self._hook_engine.trigger(
                            "PostToolUseFailure",
                            matcher_value=tool_call.function.name,
                            input_data=events.post_tool_use_failure(
                                session_id=_get_session_id(),
                                cwd=str(Path.cwd()),
                                tool_name=tool_call.function.name,
                                tool_input=tool_input_dict,
                                error=str(e),
                                tool_call_id=tool_call.id,
                            ),
                        )
                    )
                    _hook_task.add_done_callback(
                        lambda t: t.exception() if not t.cancelled() else None
                    )
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        return_value=ToolRuntimeError(str(e)),
                    )

                # --- 更新验证器状态 ---
                self._update_validator_state(tool_call.function.name, tool_input_dict)

                # --- PostToolUse 钩子触发（异步执行，不等待结果）---
                _hook_task = asyncio.create_task(
                    self._hook_engine.trigger(
                        "PostToolUse",
                        matcher_value=tool_call.function.name,
                        input_data=events.post_tool_use(
                            session_id=_get_session_id(),
                            cwd=str(Path.cwd()),
                            tool_name=tool_call.function.name,
                            tool_input=tool_input_dict,
                            tool_output=str(ret)[:2000],
                            tool_call_id=tool_call.id,
                        ),
                    )
                )
                _hook_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

                return ToolResult(tool_call_id=tool_call.id, return_value=ret)

            return asyncio.create_task(_call())
        finally:
            current_tool_call.reset(token)

    def _check_validators(self, tool_name: str, arguments: dict[str, Any]) -> ToolError | None:
        """检查工具调用是否满足验证规则。

        Args:
            tool_name: 工具名称。
            arguments: 工具调用参数。

        Returns:
            若违反验证规则返回 ToolError，否则返回 None。
        """
        for v in self._tool_validators:
            if v.tool != tool_name:
                continue
            if v.require_overview_before_detail:
                entity_id = arguments.get("entity_id", "")
                has_rel_type = bool(arguments.get("rel_type"))
                if has_rel_type and entity_id not in self._overview_queried:
                    return ToolError(
                        message=(
                            f"SearchGraph 要求先查概览再查详情。"
                            f"请先调用 SearchGraph(entity_id='{entity_id}') 获取可用关系类型列表，"
                            f"然后从中选择 rel_type 再调用详情模式。"
                        ),
                        brief="请先查概览",
                    )
        return None

    def _update_validator_state(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """工具执行成功后更新验证器状态。

        Args:
            tool_name: 工具名称。
            arguments: 工具调用参数。
        """
        if tool_name == "SearchGraph" and not arguments.get("rel_type"):
            entity_id = arguments.get("entity_id", "")
            if entity_id:
                self._overview_queried.add(entity_id)

    def register_external_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """注册外部工具到工具集。

        Args:
            name: 工具名称。
            description: 工具描述。
            parameters: 工具参数定义。

        Returns:
            元组 (成功标志, 错误信息)。成功时错误信息为 None。
        """
        if name in self._tool_dict:
            existing = self._tool_dict[name]
            if not isinstance(existing, WireExternalTool):
                return False, "tool name conflicts with existing tool"
        try:
            tool = WireExternalTool(
                name=name,
                description=description,
                parameters=parameters,
            )
        except Exception as e:
            return False, str(e)
        self.add(tool)
        return True, None

    @property
    def mcp_servers(self) -> dict[str, MCPServerInfo]:
        """获取 MCP 服务器信息字典。

        Returns:
            MCP 服务器名称到服务器信息的映射。
        """
        return self._mcp_servers

    def mcp_status_snapshot(self) -> MCPStatusSnapshot | None:
        """获取当前 MCP 启动状态的只读快照。

        Returns:
            MCP 状态快照，若无 MCP 服务器则返回 None。
        """
        if not self._mcp_servers:
            return None

        servers = tuple(
            MCPServerSnapshot(
                name=name,
                status=info.status,
                tools=tuple(tool.name for tool in info.tools),
            )
            for name, info in self._mcp_servers.items()
        )
        return MCPStatusSnapshot(
            loading=self.has_pending_mcp_tools(),
            connected=sum(1 for server in servers if server.status == "connected"),
            total=len(servers),
            tools=sum(len(server.tools) for server in servers),
            servers=servers,
        )

    def defer_mcp_tool_loading(self, mcp_configs: list[MCPConfig], runtime: Runtime) -> None:
        """存储 MCP 配置以延迟后台启动。

        Args:
            mcp_configs: MCP 配置列表。
            runtime: 运行时实例。
        """
        self._deferred_mcp_load = (list(mcp_configs), runtime)

    def has_deferred_mcp_tools(self) -> bool:
        """检查是否存在已配置但未启动的 MCP 加载。

        Returns:
            若存在延迟加载配置则返回 True，否则返回 False。
        """
        return self._deferred_mcp_load is not None

    async def start_deferred_mcp_tool_loading(self) -> bool:
        """启动延迟的 MCP 工具后台加载。

        Returns:
            若成功启动加载则返回 True，否则返回 False。
        """
        if self._deferred_mcp_load is None:
            return False
        if self._mcp_loading_task is not None or self._mcp_servers:
            self._deferred_mcp_load = None
            return False

        mcp_configs, runtime = self._deferred_mcp_load
        self._deferred_mcp_load = None
        await self.load_mcp_tools(mcp_configs, runtime, in_background=True)
        return True

    def load_tools(self, tool_paths: list[str], dependencies: dict[type[Any], Any]) -> None:
        """从指定路径加载工具。

        路径格式如 `novel_cli.tools.shell:Shell`，表示从模块加载指定类作为工具。

        Args:
            tool_paths: 工具路径列表，格式为 `模块路径:类名`。
            dependencies: 依赖注入字典，类型到实例的映射。

        Raises:
            InvalidToolError: 当工具无法加载时抛出。
        """

        good_tools: list[str] = []
        bad_tools: list[str] = []

        for tool_path in tool_paths:
            try:
                tool = self._load_tool(tool_path, dependencies)
            except SkipThisTool:
                logger.info("跳过工具: {tool_path}", tool_path=tool_path)
                continue
            if tool:
                self.add(tool)
                good_tools.append(tool_path)
            else:
                bad_tools.append(tool_path)
        logger.info("已加载工具: {good_tools}", good_tools=good_tools)
        if bad_tools:
            raise InvalidToolError(f"无效工具: {bad_tools}")

    @staticmethod
    def _load_tool(tool_path: str, dependencies: dict[type[Any], Any]) -> ToolType | None:
        """静态方法：根据路径加载单个工具。

        Args:
            tool_path: 工具路径，格式为 `模块路径:类名`。
            dependencies: 依赖注入字典。

        Returns:
            加载的工具实例，若加载失败则返回 None。

        Raises:
            ValueError: 当工具依赖无法找到时抛出。
        """
        logger.debug("加载工具: {tool_path}", tool_path=tool_path)
        module_name, class_name = tool_path.rsplit(":", 1)
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return None
        tool_cls = getattr(module, class_name, None)
        if tool_cls is None:
            return None
        args: list[Any] = []
        if "__init__" in tool_cls.__dict__:
            # 工具类覆盖了基类的 `__init__` 方法
            for param in inspect.signature(tool_cls).parameters.values():
                if param.kind == inspect.Parameter.KEYWORD_ONLY:
                    # 遇到关键字参数后停止依赖注入
                    break
                # 所有位置参数应为待注入的依赖
                if param.annotation not in dependencies:
                    raise ValueError(f"工具依赖未找到: {param.annotation}")
                args.append(dependencies[param.annotation])
        return tool_cls(*args)

    # TODO(rc): 移除 `in_background` 参数，始终在后台加载
    async def load_mcp_tools(
        self, mcp_configs: list[MCPConfig], runtime: Runtime, in_background: bool = True
    ) -> None:
        """从指定的 MCP 配置加载 MCP 工具。

        Args:
            mcp_configs: MCP 配置列表。
            runtime: 运行时实例。
            in_background: 是否在后台加载，默认为 True。

        Raises:
            MCPRuntimeError: 当 MCP 服务器无法连接时抛出。
        """
        import fastmcp
        from fastmcp.mcp_config import MCPConfig, RemoteMCPServer

        from novel_cli.ui.shell.prompt import toast

        async def _check_oauth_tokens(server_url: str) -> bool:
            """检查服务器是否存在 OAuth 令牌。

            Args:
                server_url: 服务器 URL。

            Returns:
                若存在令牌返回 True，否则返回 False。
            """
            try:
                from fastmcp.client.auth.oauth import FileTokenStorage

                storage = FileTokenStorage(server_url=server_url)
                tokens = await storage.get_tokens()
                return tokens is not None
            except Exception:
                return False

        def _toast_mcp(message: str) -> None:
            """显示 MCP 相关的 toast 提示。

            Args:
                message: 提示消息内容。
            """
            if in_background:
                toast(
                    message,
                    duration=10.0,
                    topic="mcp",
                    immediate=True,
                    position="right",
                )

        oauth_servers: dict[str, str] = {}
        """OAuth 服务器 URL 映射字典。"""

        async def _connect_server(
            server_name: str, server_info: MCPServerInfo
        ) -> tuple[str, Exception | None]:
            """连接单个 MCP 服务器。

            Args:
                server_name: 服务器名称。
                server_info: 服务器信息对象。

            Returns:
                元组 (服务器名称, 异常)。成功时异常为 None。
            """
            if server_info.status != "pending":
                return server_name, None

            server_info.status = "connecting"
            try:
                async with server_info.client as client:
                    for tool in await client.list_tools():
                        server_info.tools.append(
                            MCPTool(server_name, tool, client, runtime=runtime)
                        )

                for tool in server_info.tools:
                    self.add(tool)

                server_info.status = "connected"
                logger.info("已连接 MCP 服务器: {server_name}", server_name=server_name)
                return server_name, None
            except Exception as e:
                logger.error(
                    "连接 MCP 服务器失败: {server_name}, 错误: {error}",
                    server_name=server_name,
                    error=e,
                )
                server_info.status = "failed"
                return server_name, e

        async def _connect():
            """连接所有 MCP 服务器。"""
            _toast_mcp("正在连接 MCP 服务器...")
            unauthorized_servers: dict[str, str] = {}
            for server_name, server_info in self._mcp_servers.items():
                server_url = oauth_servers.get(server_name)
                if not server_url:
                    continue
                if not await _check_oauth_tokens(server_url):
                    logger.warning(
                        "跳过未授权的 OAuth MCP 服务器 '{server_name}'。"
                        "请先运行 'novel mcp auth {server_name}'。",
                        server_name=server_name,
                    )
                    server_info.status = "unauthorized"
                    unauthorized_servers[server_name] = server_url

            tasks = [
                asyncio.create_task(_connect_server(server_name, server_info))
                for server_name, server_info in self._mcp_servers.items()
                if server_info.status == "pending"
            ]
            results = await asyncio.gather(*tasks) if tasks else []
            failed_servers = {name: error for name, error in results if error is not None}

            for mcp_config in mcp_configs:
                # 跳过空的 MCP 配置（无服务器定义）
                if not mcp_config.mcpServers:
                    logger.debug("跳过空的 MCP 配置: {mcp_config}", mcp_config=mcp_config)
                    continue

            if failed_servers:
                _toast_mcp("MCP 连接失败")
                raise MCPRuntimeError(f"连接 MCP 服务器失败: {failed_servers}")
            if unauthorized_servers:
                _toast_mcp("MCP 需要授权")
            else:
                _toast_mcp("MCP 服务器已连接")

        for mcp_config in mcp_configs:
            if not mcp_config.mcpServers:
                logger.debug("跳过空的 MCP 配置: {mcp_config}", mcp_config=mcp_config)
                continue

            for server_name, server_config in mcp_config.mcpServers.items():
                if isinstance(server_config, RemoteMCPServer) and server_config.auth == "oauth":
                    oauth_servers[server_name] = server_config.url

                client = fastmcp.Client(MCPConfig(mcpServers={server_name: server_config}))
                self._mcp_servers[server_name] = MCPServerInfo(
                    status="pending", client=client, tools=[]
                )

        if in_background:
            self._mcp_loading_task = asyncio.create_task(_connect())
        else:
            await _connect()

    def has_pending_mcp_tools(self) -> bool:
        """检查后台 MCP 工具加载任务是否仍在运行。

        Returns:
            若后台加载任务仍在运行则返回 True，否则返回 False。
        """
        return self._mcp_loading_task is not None and not self._mcp_loading_task.done()

    async def wait_for_mcp_tools(self) -> None:
        """等待后台 MCP 工具加载完成。"""
        task = self._mcp_loading_task
        if not task:
            return
        try:
            await task
        finally:
            if self._mcp_loading_task is task and task.done():
                self._mcp_loading_task = None

    async def cleanup(self) -> None:
        """清理工具集持有的所有资源。"""
        self._deferred_mcp_load = None
        if self._mcp_loading_task:
            self._mcp_loading_task.cancel()
            with contextlib.suppress(Exception):
                await self._mcp_loading_task
        for server_info in self._mcp_servers.values():
            await server_info.client.close()


@dataclass(slots=True)
class MCPServerInfo:
    """MCP 服务器信息数据类。

    Attributes:
        status: 服务器状态，可选值为 pending、connecting、connected、failed、unauthorized。
        client: fastmcp 客户端实例。
        tools: 服务器提供的工具列表。
    """

    status: Literal["pending", "connecting", "connected", "failed", "unauthorized"]
    client: fastmcp.Client[Any]
    tools: list[MCPTool[Any]]


class MCPTool[T: ClientTransport](CallableTool):
    """MCP 协议工具包装类。

    将 MCP 服务器提供的工具包装为可调用的工具实例。

    Attributes:
        _mcp_tool: MCP 工具原始对象。
        _client: fastmcp 客户端实例。
        _runtime: 运行时实例。
        _timeout: 工具调用超时时间。
        _action_name: 操作名称标识。
    """

    def __init__(
        self,
        server_name: str,
        mcp_tool: mcp.Tool,
        client: fastmcp.Client[T],
        *,
        runtime: Runtime,
        **kwargs: Any,
    ):
        """初始化 MCP 工具实例。

        Args:
            server_name: MCP 服务器名称。
            mcp_tool: MCP 工具原始对象。
            client: fastmcp 客户端实例。
            runtime: 运行时实例。
            **kwargs: 其他传递给基类的参数。
        """
        super().__init__(
            name=mcp_tool.name,
            description=(
                f"This is an MCP (Model Context Protocol) tool from MCP server `{server_name}`.\n\n"
                f"{mcp_tool.description or 'No description provided.'}"
            ),
            parameters=mcp_tool.inputSchema,
            **kwargs,
        )
        self._mcp_tool = mcp_tool
        self._client = client
        self._runtime = runtime
        self._timeout = timedelta(milliseconds=runtime.config.mcp.client.tool_call_timeout_ms)
        self._action_name = f"mcp:{mcp_tool.name}"

    async def __call__(self, *args: Any, **kwargs: Any) -> ToolReturnValue:
        """执行 MCP 工具调用。

        Args:
            *args: 位置参数（通常不使用）。
            **kwargs: 工具调用参数。

        Returns:
            工具调用返回值或错误信息。
        """
        description = f"Call MCP tool `{self._mcp_tool.name}`."
        result = await self._runtime.approval.request(self.name, self._action_name, description)
        if not result:
            return result.rejection_error()

        try:
            async with self._client as client:
                result = await client.call_tool(
                    self._mcp_tool.name,
                    kwargs,
                    timeout=self._timeout,
                    raise_on_error=False,
                )
                return convert_mcp_tool_result(result)
        except Exception as e:
            # fastmcp 在超时时抛出 `RuntimeError`，无法与其他错误区分
            exc_msg = str(e).lower()
            if "timeout" in exc_msg or "timed out" in exc_msg:
                return ToolError(
                    message=(
                        f"Timeout while calling MCP tool `{self._mcp_tool.name}`. "
                        "You may explain to the user that the timeout config is set too low."
                    ),
                    brief="Timeout",
                )
            raise


class WireExternalTool(CallableTool):
    """外部工具 Wire 通信包装类。

    用于处理通过 Wire 协议进行的外部工具调用。

    Attributes:
        无额外属性，继承基类属性。
    """

    def __init__(self, *, name: str, description: str, parameters: dict[str, Any]) -> None:
        """初始化外部工具实例。

        Args:
            name: 工具名称。
            description: 工具描述。
            parameters: 工具参数定义。
        """
        super().__init__(
            name=name,
            description=description or "No description provided.",
            parameters=parameters,
        )

    async def __call__(self, *args: Any, **kwargs: Any) -> ToolReturnValue:
        """执行外部工具调用。

        通过 Wire 协议将工具调用请求发送到外部处理器。

        Args:
            *args: 位置参数（通常不使用）。
            **kwargs: 工具调用参数。

        Returns:
            工具调用返回值或错误信息。
        """
        tool_call = get_current_tool_call_or_none()
        if tool_call is None:
            return ToolError(
                message="External tool calls must be invoked from a tool call context.",
                brief="Invalid tool call",
            )

        from novel_cli.soul import get_wire_or_none

        wire = get_wire_or_none()
        if wire is None:
            logger.error(
                "Wire 不可用于外部工具调用: {tool_name}", tool_name=self.name
            )
            return ToolError(
                message="Wire is not available for external tool calls.",
                brief="Wire unavailable",
            )

        external_tool_call = ToolCallRequest.from_tool_call(tool_call)
        wire.soul_side.send(external_tool_call)
        try:
            return await external_tool_call.wait()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("外部工具调用失败: {tool_name}:", tool_name=self.name)
            return ToolError(
                message=f"External tool call failed: {e}",
                brief="External tool error",
            )


def convert_mcp_tool_result(result: CallToolResult) -> ToolReturnValue:
    """将 MCP 工具结果转换为 kosong 工具返回值。

    Args:
        result: MCP 工具调用结果。

    Returns:
        转换后的工具返回值，可能为 ToolOk 或 ToolError。

    Raises:
        ValueError: 当内容部分包含不支持的类型或 MIME 类型时抛出。
    """
    content: list[ContentPart] = []
    for part in result.content:
        content.append(convert_mcp_content(part))
    if result.is_error:
        return ToolError(
            output=content,
            message="Tool returned an error. The output may be error message or incomplete output",
            brief="",
        )
    else:
        return ToolOk(output=content)
