from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import warnings
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import kaos
from kaos.path import KaosPath
from pydantic import SecretStr

from novel_cli.agentspec import DEFAULT_AGENT_FILE, load_agent_spec
from novel_cli.cli import InputFormat, OutputFormat
from novel_cli.config import Config, LLMModel, LLMProvider, load_config
from novel_cli.llm import augment_provider_with_env_vars, create_llm, model_display_name
from novel_cli.exception import NovelCLIException
from novel_cli.session import Session
from novel_cli.share import get_share_dir
from novel_cli.soul import run_soul
from novel_cli.soul.agent import Runtime, load_agent
from novel_cli.soul.context import Context
from novel_cli.soul.novelsoul import NovelSoul
from novel_cli.store import NovelStore
from novel_cli.utils.aioqueue import QueueShutDown
from novel_cli.utils.logging import logger, redirect_stderr_to_logger
from novel_cli.utils.path import shorten_home
from novel_cli.wire import Wire, WireUISide
from novel_cli.wire.types import ApprovalRequest, ApprovalResponse, ContentPart, WireMessage

if TYPE_CHECKING:
    from fastmcp.mcp_config import MCPConfig


def enable_logging(debug: bool = False, *, redirect_stderr: bool = True) -> None:
    # NOTE: stderr redirection is implemented by swapping the process-level fd=2 (dup2).
    # That can hide Click/Typer error output during CLI startup, so some entrypoints delay
    # installing it until after critical initialization succeeds.
    logger.remove()  # Remove default stderr handler
    logger.enable("novel_cli")
    if debug:
        logger.enable("kosong")
    logger.add(
        get_share_dir() / "logs" / "novel.log",
        # FIXME: configure level for different modules
        level="TRACE" if debug else "INFO",
        rotation="06:00",
        retention="10 days",
    )
    if redirect_stderr:
        redirect_stderr_to_logger()


def _cleanup_stale_foreground_subagents(runtime: Runtime) -> None:
    subagent_store = getattr(runtime, "subagent_store", None)
    if subagent_store is None:
        return

    stale_agent_ids = [
        record.agent_id
        for record in subagent_store.list_instances()
        if record.status == "running_foreground"
    ]
    for agent_id in stale_agent_ids:
        logger.warning(
            "Marking stale foreground subagent instance as failed during startup: {agent_id}",
            agent_id=agent_id,
        )
        subagent_store.update_instance(agent_id, status="failed")


class NovelCLI:
    @staticmethod
    async def create(
        session: Session,
        *,
        # Basic configuration
        config: Config | Path | None = None,
        model_name: str | None = None,
        thinking: bool | None = None,
        # Run mode
        yolo: bool = False,
        plan_mode: bool = False,
        resumed: bool = False,
        # Extensions
        agent_file: Path | None = None,
        mcp_configs: list[MCPConfig] | list[dict[str, Any]] | None = None,
        skills_dirs: list[KaosPath] | None = None,
        # Loop control
        max_steps_per_turn: int | None = None,
        max_retries_per_step: int | None = None,
        max_ralph_iterations: int | None = None,
        startup_progress: Callable[[str], None] | None = None,
        defer_mcp_loading: bool = False,
        # Book selection
        book_name: str | None = None,
    ) -> NovelCLI:
        """
        Create a NovelCLI instance.

        Args:
            session (Session): A session created by `Session.create` or `Session.continue_`.
            config (Config | Path | None, optional): Configuration to use, or path to config file.
                Defaults to None.
            model_name (str | None, optional): Name of the model to use. Defaults to None.
            thinking (bool | None, optional): Whether to enable thinking mode. Defaults to None.
            yolo (bool, optional): Approve all actions without confirmation. Defaults to False.
            agent_file (Path | None, optional): Path to the agent file. Defaults to None.
            mcp_configs (list[MCPConfig | dict[str, Any]] | None, optional): MCP configs to load
                MCP tools from. Defaults to None.
            skills_dirs (list[KaosPath] | None, optional): Custom skills directories that
                override default user/project discovery. Defaults to None.
            max_steps_per_turn (int | None, optional): Maximum number of steps in one turn.
                Defaults to None.
            max_retries_per_step (int | None, optional): Maximum number of retries in one step.
                Defaults to None.
            max_ralph_iterations (int | None, optional): Extra iterations after the first turn in
                Ralph mode. Defaults to None.
            startup_progress (Callable[[str], None] | None, optional): Progress callback used by
                interactive startup UI. Defaults to None.
            defer_mcp_loading (bool, optional): Defer MCP startup until the interactive shell is
                ready. Defaults to False.
            book_name (str | None, optional): 书名，用于指定初始选中的书籍。Defaults to None.

        Raises:
            FileNotFoundError: When the agent file is not found.
            ConfigError(NovelCLIException, ValueError): When the configuration is invalid.
            AgentSpecError(NovelCLIException, ValueError): When the agent specification is invalid.
            SystemPromptTemplateError(NovelCLIException, ValueError): When the system prompt
                template is invalid.
            InvalidToolError(NovelCLIException, ValueError): When any tool cannot be loaded.
            MCPConfigError(NovelCLIException, ValueError): When any MCP configuration is invalid.
            MCPRuntimeError(NovelCLIException, RuntimeError): When any MCP server cannot be
                connected.
        """
        if startup_progress is not None:
            startup_progress("Loading configuration...")

        config = config if isinstance(config, Config) else load_config(config)
        if max_steps_per_turn is not None:
            config.loop_control.max_steps_per_turn = max_steps_per_turn
        if max_retries_per_step is not None:
            config.loop_control.max_retries_per_step = max_retries_per_step
        if max_ralph_iterations is not None:
            config.loop_control.max_ralph_iterations = max_ralph_iterations
        logger.info("Loaded config: {config}", config=config)

        model: LLMModel | None = None
        provider: LLMProvider | None = None

        # try to use config file
        if not model_name and config.default_model:
            # no --model specified && default model is set in config
            model = config.models[config.default_model]
            provider = config.providers[model.provider]
        if model_name and model_name in config.models:
            # --model specified && model is set in config
            model = config.models[model_name]
            provider = config.providers[model.provider]

        if not model:
            model = LLMModel(provider="", model="", max_context_size=100_000)
            provider = LLMProvider(type="kimi", base_url="", api_key=SecretStr(""))

        # try overwrite with environment variables
        assert provider is not None
        assert model is not None
        env_overrides = augment_provider_with_env_vars(provider, model)

        # determine thinking mode
        thinking = config.default_thinking if thinking is None else thinking

        # determine yolo mode
        yolo = yolo if yolo else config.default_yolo

        # determine plan mode (only for new sessions, not restored)
        if not resumed:
            plan_mode = plan_mode if plan_mode else config.default_plan_mode

        llm = create_llm(
            provider,
            model,
            thinking=thinking,
            session_id=session.id,
        )
        if llm is not None:
            logger.info("Using LLM provider: {provider}", provider=provider)
            logger.info("Using LLM model: {model}", model=model)
            logger.info("Thinking mode: {thinking}", thinking=thinking)

        if startup_progress is not None:
            startup_progress("Scanning workspace...")

        runtime = await Runtime.create(
            config,
            llm,
            session,
            yolo,
            skills_dirs=skills_dirs,
        )
        runtime.notifications.recover()
        runtime.background_tasks.reconcile()
        _cleanup_stale_foreground_subagents(runtime)

        # Refresh plugin configs with fresh credentials
        try:
            from novel_cli.plugin.manager import (
                collect_host_values,
                get_plugins_dir,
                refresh_plugin_configs,
            )

            host_values = collect_host_values(config)
            if host_values.get("api_key"):
                refresh_plugin_configs(get_plugins_dir(), host_values)
        except Exception:
            logger.debug("Failed to refresh plugin configs, skipping")

        if agent_file is None:
            agent_file = DEFAULT_AGENT_FILE
        if startup_progress is not None:
            startup_progress("Loading agent...")

        agent = await load_agent(
            agent_file,
            runtime,
            mcp_configs=mcp_configs or [],
            start_mcp_loading=not defer_mcp_loading,
        )

        # 校验 --book 参数
        if book_name is not None:
            # 检查 agent spec 是否声明了小说搜索工具
            agent_spec = load_agent_spec(agent_file)
            agent_tools = agent_spec.tools or []
            agent_allowed = agent_spec.allowed_tools or []
            _search_tool_names = ("SearchEntity", "SearchGraph", "SearchCorpus")
            # 检查工具声明中是否包含小说搜索工具
            has_search_novel_declared = any(
                name in t for t in agent_tools + agent_allowed for name in _search_tool_names
            )

            if has_search_novel_declared:
                # agent 声明了小说搜索工具，检查数据库配置
                novel_db = config.services.novel_db
                if not novel_db.pg_password.get_secret_value():
                    raise NovelCLIException("小说知识库未配置，无法校验 --book 指定的书名")

                # 如果小说搜索工具已加载到 toolset，校验书名
                if any(agent.toolset.find(n) is not None for n in _search_tool_names):
                    store = NovelStore(novel_db)
                    try:
                        await store.connect()
                        books = await store.list_books()
                    finally:
                        await store.close()

                    if book_name not in books:
                        raise NovelCLIException(
                            f"未找到书籍: {book_name}\n可用书籍: {', '.join(books)}"
                        )

                    session.state.current_book = book_name
                    session.save_state()
                else:
                    # 小说搜索工具已声明但未加载（可能因其他原因被跳过）
                    logger.warning("--book 参数被忽略：小说搜索工具未加载")
            else:
                # agent 未声明小说搜索工具，打 warning log
                logger.warning("--book 参数被忽略：当前 agent 不支持书籍查询")

        if startup_progress is not None:
            startup_progress("Restoring conversation...")
        context = Context(session.context_file)
        await context.restore()

        if context.system_prompt is not None:
            agent = dataclasses.replace(agent, system_prompt=context.system_prompt)
        else:
            await context.write_system_prompt(agent.system_prompt)

        soul = NovelSoul(agent, context=context)

        # Activate plan mode if requested (for new sessions or --plan flag)
        if plan_mode and not soul.plan_mode:
            await soul.set_plan_mode_from_manual(True)
        elif plan_mode and soul.plan_mode:
            # Already in plan mode from restored session, trigger activation reminder
            soul.schedule_plan_activation_reminder()

        # Create and inject hook engine
        from novel_cli.hooks.engine import HookEngine

        hook_engine = HookEngine(config.hooks, cwd=str(session.work_dir))
        soul.set_hook_engine(hook_engine)
        runtime.hook_engine = hook_engine

        return NovelCLI(soul, runtime, env_overrides)

    def __init__(
        self,
        _soul: NovelSoul,
        _runtime: Runtime,
        _env_overrides: dict[str, str],
    ) -> None:
        self._soul = _soul
        self._runtime = _runtime
        self._env_overrides = _env_overrides

    @property
    def soul(self) -> NovelSoul:
        """Get the NovelSoul instance."""
        return self._soul

    @property
    def session(self) -> Session:
        """Get the Session instance."""
        return self._runtime.session

    def shutdown_background_tasks(self) -> None:
        """Kill active background tasks on exit, unless keep_alive_on_exit is configured."""
        if self._runtime.config.background.keep_alive_on_exit:
            return
        killed = self._runtime.background_tasks.kill_all_active(reason="CLI session ended")
        if killed:
            logger.info("Stopped {n} background task(s) on exit: {ids}", n=len(killed), ids=killed)

    @contextlib.asynccontextmanager
    async def _env(self) -> AsyncGenerator[None]:
        original_cwd = KaosPath.cwd()
        await kaos.chdir(self._runtime.session.work_dir)
        try:
            # to ignore possible warnings from dateparser
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            yield
        finally:
            await kaos.chdir(original_cwd)

    async def run(
        self,
        user_input: str | list[ContentPart],
        cancel_event: asyncio.Event,
        merge_wire_messages: bool = False,
    ) -> AsyncGenerator[WireMessage]:
        """
        Run the Novel CLI instance without any UI and yield Wire messages directly.

        Args:
            user_input (str | list[ContentPart]): The user input to the agent.
            cancel_event (asyncio.Event): An event to cancel the run.
            merge_wire_messages (bool): Whether to merge Wire messages as much as possible.

        Yields:
            WireMessage: The Wire messages from the `NovelSoul`.

        Raises:
            LLMNotSet: When the LLM is not set.
            LLMNotSupported: When the LLM does not have required capabilities.
            ChatProviderError: When the LLM provider returns an error.
            MaxStepsReached: When the maximum number of steps is reached.
            RunCancelled: When the run is cancelled by the cancel event.
        """
        async with self._env():
            wire_future = asyncio.Future[WireUISide]()
            stop_ui_loop = asyncio.Event()
            approval_bridge_tasks: dict[str, asyncio.Task[None]] = {}
            forwarded_approval_requests: dict[str, ApprovalRequest] = {}

            async def _bridge_approval_request(request: ApprovalRequest) -> None:
                try:
                    response = await request.wait()
                    assert self._runtime.approval_runtime is not None
                    self._runtime.approval_runtime.resolve(
                        request.id, response, feedback=request.feedback
                    )
                finally:
                    approval_bridge_tasks.pop(request.id, None)
                    forwarded_approval_requests.pop(request.id, None)

            def _forward_approval_request(wire: Wire, request: ApprovalRequest) -> None:
                if request.id in forwarded_approval_requests:
                    return
                forwarded_approval_requests[request.id] = request
                if request.id not in approval_bridge_tasks:
                    approval_bridge_tasks[request.id] = asyncio.create_task(
                        _bridge_approval_request(request)
                    )
                wire.soul_side.send(request)

            async def _ui_loop_fn(wire: Wire) -> None:
                wire_future.set_result(wire.ui_side(merge=merge_wire_messages))
                assert self._runtime.root_wire_hub is not None
                assert self._runtime.approval_runtime is not None
                root_hub_queue = self._runtime.root_wire_hub.subscribe()
                stop_task = asyncio.create_task(stop_ui_loop.wait())
                queue_task = asyncio.create_task(root_hub_queue.get())
                try:
                    for pending in self._runtime.approval_runtime.list_pending():
                        _forward_approval_request(
                            wire,
                            ApprovalRequest(
                                id=pending.id,
                                tool_call_id=pending.tool_call_id,
                                sender=pending.sender,
                                action=pending.action,
                                description=pending.description,
                                display=pending.display,
                                source_kind=pending.source.kind,
                                source_id=pending.source.id,
                                agent_id=pending.source.agent_id,
                                subagent_type=pending.source.subagent_type,
                            ),
                        )
                    while True:
                        done, _ = await asyncio.wait(
                            [stop_task, queue_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if stop_task in done:
                            break
                        try:
                            msg = queue_task.result()
                        except QueueShutDown:
                            break
                        match msg:
                            case ApprovalRequest() as request:
                                _forward_approval_request(wire, request)
                                queue_task = asyncio.create_task(root_hub_queue.get())
                                continue
                            case ApprovalResponse() as response:
                                if (
                                    request := forwarded_approval_requests.get(response.request_id)
                                ) and not request.resolved:
                                    request.resolve(response.response, response.feedback)
                            case _:
                                pass
                        wire.soul_side.send(msg)
                        queue_task = asyncio.create_task(root_hub_queue.get())
                finally:
                    stop_task.cancel()
                    queue_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await stop_task
                    with contextlib.suppress(asyncio.CancelledError):
                        await queue_task
                    for task in list(approval_bridge_tasks.values()):
                        task.cancel()
                    for task in list(approval_bridge_tasks.values()):
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                    approval_bridge_tasks.clear()
                    forwarded_approval_requests.clear()
                    assert self._runtime.root_wire_hub is not None
                    self._runtime.root_wire_hub.unsubscribe(root_hub_queue)

            soul_task = asyncio.create_task(
                run_soul(
                    self.soul,
                    user_input,
                    _ui_loop_fn,
                    cancel_event,
                    runtime=self._runtime,
                )
            )

            try:
                wire_ui = await wire_future
                while True:
                    msg = await wire_ui.receive()
                    yield msg
            except QueueShutDown:
                pass
            finally:
                # stop consuming Wire messages
                stop_ui_loop.set()
                # wait for the soul task to finish, or raise
                await soul_task

    async def run_shell(
        self, command: str | None = None, *, prefill_text: str | None = None
    ) -> bool:
        """Run the Novel CLI instance with shell UI."""
        from novel_cli.ui.shell import Shell, WelcomeInfoItem

        welcome_info = [
            WelcomeInfoItem(
                name="Directory", value=str(shorten_home(self._runtime.session.work_dir))
            ),
            WelcomeInfoItem(name="Session", value=self._runtime.session.id),
        ]
        if base_url := self._env_overrides.get("NOVEL_BASE_URL"):
            welcome_info.append(
                WelcomeInfoItem(
                    name="API URL",
                    value=f"{base_url} (from NOVEL_BASE_URL)",
                    level=WelcomeInfoItem.Level.WARN,
                )
            )
        if self._env_overrides.get("NOVEL_API_KEY"):
            welcome_info.append(
                WelcomeInfoItem(
                    name="API Key",
                    value="****** (from NOVEL_API_KEY)",
                    level=WelcomeInfoItem.Level.WARN,
                )
            )
        if not self._runtime.llm:
            welcome_info.append(
                WelcomeInfoItem(
                    name="Model",
                    value="not set, run 'novel setup' to configure",
                    level=WelcomeInfoItem.Level.WARN,
                )
            )
        elif "NOVEL_MODEL_NAME" in self._env_overrides:
            welcome_info.append(
                WelcomeInfoItem(
                    name="Model",
                    value=f"{self._soul.model_name} (from NOVEL_MODEL_NAME)",
                    level=WelcomeInfoItem.Level.WARN,
                )
            )
        else:
            welcome_info.append(
                WelcomeInfoItem(
                    name="Model",
                    value=model_display_name(self._soul.model_name),
                    level=WelcomeInfoItem.Level.INFO,
                )
            )
        # Book
        book = self._runtime.session.state.current_book
        if book is None:
            welcome_info.append(
                WelcomeInfoItem(
                    name="Book",
                    value="未选择，使用 /book 选择书籍",
                    level=WelcomeInfoItem.Level.WARN,
                )
            )
        else:
            welcome_info.append(
                WelcomeInfoItem(name="Book", value=book)
            )
        welcome_info.append(
            WelcomeInfoItem(
                name="\nTip",
                value=(
                    "Spot a bug or have feedback? Type /feedback right in this session"
                    " — every report makes Novel better."
                ),
                level=WelcomeInfoItem.Level.INFO,
            )
        )
        async with self._env():
            shell = Shell(self._soul, welcome_info=welcome_info, prefill_text=prefill_text)
            return await shell.run(command)

    async def run_print(
        self,
        input_format: InputFormat,
        output_format: OutputFormat,
        command: str | None = None,
        *,
        final_only: bool = False,
    ) -> int:
        """Run the Novel CLI instance with print UI."""
        from novel_cli.ui.print import Print

        async with self._env():
            print_ = Print(
                self._soul,
                input_format,
                output_format,
                self._runtime.session.context_file,
                final_only=final_only,
            )
            return await print_.run(command)

    async def run_acp(self) -> None:
        """Run the Novel CLI instance as ACP server."""
        from novel_cli.ui.acp import ACP

        async with self._env():
            acp = ACP(self._soul)
            await acp.run()

    async def run_wire_stdio(self) -> None:
        """Run the Novel CLI instance as Wire server over stdio."""
        from novel_cli.wire.server import WireServer

        async with self._env():
            server = WireServer(self._soul)
            await server.serve()
