"""CLI 主入口模块。

提供 Novel CLI 的主命令和所有子命令的入口。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import typer

if TYPE_CHECKING:
    from novel_cli.config import Config
    from novel_cli.session import Session

from ._lazy_group import LazySubcommandGroup


class Reload(Exception):
    """重新加载配置异常。

    用于触发配置重新加载和会话切换。

    Attributes:
        session_id: 要切换到的会话 ID。
        prefill_text: 预填充的输入文本。
        source_session: 来源会话对象。
    """

    def __init__(self, session_id: str | None = None, prefill_text: str | None = None):
        super().__init__("reload")
        self.session_id = session_id
        self.prefill_text = prefill_text
        self.source_session: Session | None = None


class SwitchToWeb(Exception):
    """切换到 web 界面异常。

    用于触发从 shell 界面切换到 web 界面。

    Attributes:
        session_id: 当前会话 ID。
    """

    def __init__(self, session_id: str | None = None):
        super().__init__("switch_to_web")
        self.session_id = session_id


class SwitchToVis(Exception):
    """切换到可视化界面异常。

    用于触发从 shell 界面切换到 tracing visualizer 界面。

    Attributes:
        session_id: 当前会话 ID。
    """

    def __init__(self, session_id: str | None = None):
        super().__init__("switch_to_vis")
        self.session_id = session_id


cli = typer.Typer(
    cls=LazySubcommandGroup,
    epilog="""\b\
文档：        https://moonshotai.github.io/novel-cli/\n
LLM 友好版本： https://moonshotai.github.io/novel-cli/llms.txt""",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Novel，您的下一个 CLI agent。",
)

UIMode = Literal["shell", "print", "acp", "wire"]


class ExitCode:
    """退出码常量类。

    Attributes:
        SUCCESS: 成功退出码 (0)。
        FAILURE: 失败退出码 (1)。
        RETRYABLE: 可重试退出码 (75，来自 sysexits.h 的 EX_TEMPFAIL)。
    """

    SUCCESS = 0
    FAILURE = 1
    RETRYABLE = 75  # EX_TEMPFAIL（来自 sysexits.h）


InputFormat = Literal["text", "stream-json"]
OutputFormat = Literal["text", "stream-json"]


def _strip_session_id_suffix(title: str, session_id: str) -> str:
    """移除会话标题末尾的 `` (session_id)`` 后缀（如果存在）。

    Args:
        title: 会话标题。
        session_id: 会话 ID。

    Returns:
        移除后缀后的标题。
    """
    suffix = f" ({session_id})"
    return title.rsplit(suffix, 1)[0] if title.endswith(suffix) else title


def _version_callback(value: bool) -> None:
    """版本选项回调函数。

    Args:
        value: 是否显示版本。

    Raises:
        typer.Exit: 当 value 为 True 时退出并显示版本。
    """
    if value:
        from novel_cli.constant import get_version

        typer.echo(f"novel, version {get_version()}")
        raise typer.Exit()


def _is_first_run(config: Config) -> bool:
    """检查用户是否需要进行首次设置引导。

    Args:
        config: 配置对象。

    Returns:
        是否为首次运行（无 provider、无 model、使用默认配置位置）。
    """
    return (
        not config.providers  # 无 provider
        and not config.models  # 无 model
        and config.is_from_default_location  # 使用默认配置位置（非 --config）
    )


@cli.callback(invoke_without_command=True)
def novel(
    ctx: typer.Context,
    # 元信息
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="显示版本并退出。",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="打印详细信息。默认：否。",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="记录调试信息。默认：否。",
        ),
    ] = False,
    # 基础配置
    local_work_dir: Annotated[
        Path | None,
        typer.Option(
            "--work-dir",
            "-w",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            writable=True,
            help="agent 的工作目录。默认：当前目录。",
        ),
    ] = None,
    local_add_dirs: Annotated[
        list[Path] | None,
        typer.Option(
            "--add-dir",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help=(
                "添加额外目录到工作空间范围。"
                "可多次指定。"
            ),
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        typer.Option(
            "--session",
            "--resume",
            "-S",
            "-r",
            help=(
                "恢复会话。"
                "带 ID：恢复指定会话。"
                "不带 ID：交互式选择会话。"
            ),
        ),
    ] = None,
    continue_: Annotated[
        bool,
        typer.Option(
            "--continue",
            "-C",
            help="继续工作目录的上一个会话。默认：否。",
        ),
    ] = False,
    config_string: Annotated[
        str | None,
        typer.Option(
            "--config",
            help="要加载的配置 TOML/JSON 字符串。默认：无。",
        ),
    ] = None,
    config_file: Annotated[
        Path | None,
        typer.Option(
            "--config-file",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="要加载的配置 TOML/JSON 文件。默认：~/.novel/config.toml。",
        ),
    ] = None,
    model_name: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="要使用的 LLM 模型。默认：配置文件中设置的默认模型。",
        ),
    ] = None,
    thinking: Annotated[
        bool | None,
        typer.Option(
            "--thinking/--no-thinking",
            help="启用思考模式。默认：配置文件中设置的默认思考模式。",
        ),
    ] = None,
    # 运行模式
    yolo: Annotated[
        bool,
        typer.Option(
            "--yolo",
            "--yes",
            "-y",
            "--auto-approve",
            help="自动批准所有操作。默认：否。",
        ),
    ] = False,
    plan: Annotated[
        bool,
        typer.Option(
            "--plan",
            help="以计划模式启动。默认：否。",
        ),
    ] = False,
    prompt: Annotated[
        str | None,
        typer.Option(
            "--prompt",
            "-p",
            "--command",
            "-c",
            help="给 agent 的用户提示。默认：交互式提示。",
        ),
    ] = None,
    print_mode: Annotated[
        bool,
        typer.Option(
            "--print",
            help=(
                "以打印模式运行（非交互）。注意：打印模式隐式添加 `--yolo`。"
            ),
        ),
    ] = False,
    acp_mode: Annotated[
        bool,
        typer.Option(
            "--acp",
            help="（已弃用，请使用 `novel acp`）以 ACP server 运行。",
        ),
    ] = False,
    wire_mode: Annotated[
        bool,
        typer.Option(
            "--wire",
            help="以 Wire server 运行（实验性）。",
        ),
    ] = False,
    input_format: Annotated[
        InputFormat | None,
        typer.Option(
            "--input-format",
            help=(
                "使用的输入格式。必须与 `--print` 配合使用"
                "且输入需通过 stdin 管道传入。"
                "默认：text。"
            ),
        ),
    ] = None,
    output_format: Annotated[
        OutputFormat | None,
        typer.Option(
            "--output-format",
            help="使用的输出格式。必须与 `--print` 配合使用。默认：text。",
        ),
    ] = None,
    final_message_only: Annotated[
        bool,
        typer.Option(
            "--final-message-only",
            help="仅打印最终的 assistant 消息（print UI）。",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            help="等同于 `--print --output-format text --final-message-only`。",
        ),
    ] = False,
    # 定制化
    book: Annotated[
        str | None,
        typer.Option(
            "--book",
            help="启动时指定书籍名称。",
        ),
    ] = None,
    agent: Annotated[
        Literal["default"] | None,
        typer.Option(
            "--agent",
            help="要使用的内置 agent 规格。默认：内置默认 agent。",
        ),
    ] = None,
    agent_file: Annotated[
        Path | None,
        typer.Option(
            "--agent-file",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="自定义 agent 规格文件。默认：内置默认 agent。",
        ),
    ] = None,
    mcp_config_file: Annotated[
        list[Path] | None,
        typer.Option(
            "--mcp-config-file",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help=(
                "要加载的 MCP 配置文件。可多次指定以添加多个 MCP "
                "配置。默认：无。"
            ),
        ),
    ] = None,
    mcp_config: Annotated[
        list[str] | None,
        typer.Option(
            "--mcp-config",
            help=(
                "要加载的 MCP 配置 JSON。可多次指定以添加多个 MCP "
                "配置。默认：无。"
            ),
        ),
    ] = None,
    local_skills_dir: Annotated[
        list[Path] | None,
        typer.Option(
            "--skills-dir",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="自定义 skills 目录（可重复指定）。覆盖默认发现。",
        ),
    ] = None,
    # 循环控制
    max_steps_per_turn: Annotated[
        int | None,
        typer.Option(
            "--max-steps-per-turn",
            min=1,
            help="单次 turn 的最大步数。默认：从配置读取。",
        ),
    ] = None,
    max_retries_per_step: Annotated[
        int | None,
        typer.Option(
            "--max-retries-per-step",
            min=1,
            help="单步的最大重试次数。默认：从配置读取。",
        ),
    ] = None,
    max_ralph_iterations: Annotated[
        int | None,
        typer.Option(
            "--max-ralph-iterations",
            min=-1,
            help=(
                "Ralph 模式下首次 turn 后的额外迭代次数。-1 表示无限。"
                "默认：从配置读取。"
            ),
        ),
    ] = None,
):
    """Novel，您的下一个 CLI agent。

    Args:
        ctx: Typer 上下文对象。
        version: 是否显示版本。
        verbose: 是否打印详细信息。
        debug: 是否记录调试信息。
        local_work_dir: 工作目录。
        local_add_dirs: 额外目录列表。
        session_id: 会话 ID。
        continue_: 是否继续上一个会话。
        config_string: 配置字符串。
        config_file: 配置文件路径。
        model_name: 模型名称。
        thinking: 是否启用思考模式。
        yolo: 是否自动批准所有操作。
        plan: 是否以计划模式启动。
        prompt: 用户提示。
        print_mode: 是否以打印模式运行。
        acp_mode: 是否以 ACP server 运行。
        wire_mode: 是否以 Wire server 运行。
        input_format: 输入格式。
        output_format: 输出格式。
        final_message_only: 是否仅打印最终消息。
        quiet: 是否静默模式。
        book: 启动时指定书籍名称。
        agent: 内置 agent 名称。
        agent_file: agent 文件路径。
        mcp_config_file: MCP 配置文件列表。
        mcp_config: MCP 配置 JSON 列表。
        local_skills_dir: skills 目录列表。
        max_steps_per_turn: 单次 turn 最大步数。
        max_retries_per_step: 单步最大重试次数。
        max_ralph_iterations: Ralph 模式额外迭代次数。
    """
    import asyncio
    import contextlib
    import json

    from novel_cli.utils.proctitle import init_process_name

    init_process_name("Novel Code")

    if ctx.invoked_subcommand is not None:
        return  # 如果调用了子命令则跳过后续处理

    del version  # 在回调中已处理

    from kaos.path import KaosPath

    from novel_cli.agentspec import DEFAULT_AGENT_FILE
    from novel_cli.app import NovelCLI, enable_logging
    from novel_cli.config import Config, load_config_from_string
    from novel_cli.exception import ConfigError
    from novel_cli.hooks import events as hook_events
    from novel_cli.metadata import load_metadata, save_metadata
    from novel_cli.session import Session
    from novel_cli.ui.shell.startup import ShellStartupProgress
    from novel_cli.utils.logging import logger, open_original_stderr, redirect_stderr_to_logger

    from .mcp import get_global_mcp_config_file

    # 不要在参数解析期间重定向 stderr。我们的 stderr 重定向器
    # 会将 fd=2 替换为管道，这会吞掉 Click/Typer 启动错误。
    # 重定向在 NovelCLI.create() 之前安装，这样
    # MCP server stderr 噪声从一开始就被记录到日志中。
    enable_logging(debug, redirect_stderr=False)

    def _emit_fatal_error(message: str) -> None:
        # 优先写入原始 stderr fd，即使后续我们重定向了 fd=2。
        # 这确保致命错误对用户可见。
        with open_original_stderr() as stream:
            if stream is not None:
                stream.write((message.rstrip() + "\n").encode("utf-8", errors="replace"))
                stream.flush()
                return
        typer.echo(message, err=True)

    # session_id 状态：
    #   None  → 未提供（新建会话）
    #   ""    → --session/--resume 无值（选择器模式）
    #   "ID"  → --session ID（恢复指定会话）
    _picker_mode = session_id == ""
    if session_id is not None:
        session_id = session_id.strip() or None  # 将仅空白字符视为选择器模式
        if session_id is None:
            _picker_mode = True

    if quiet:
        if acp_mode or wire_mode:
            raise typer.BadParameter(
                "Quiet mode cannot be combined with ACP or Wire UI",
                param_hint="--quiet",
            )
        if output_format not in (None, "text"):
            raise typer.BadParameter(
                "Quiet mode implies `--output-format text`",
                param_hint="--quiet",
            )
        print_mode = True
        output_format = "text"
        final_message_only = True

    conflict_option_sets = [
        {
            "--print": print_mode,
            "--acp": acp_mode,
            "--wire": wire_mode,
        },
        {
            "--agent": agent is not None,
            "--agent-file": agent_file is not None,
        },
        {
            "--continue": continue_,
            "--session": session_id is not None or _picker_mode,
        },
        {
            "--config": config_string is not None,
            "--config-file": config_file is not None,
        },
    ]
    for option_set in conflict_option_sets:
        active_options = [flag for flag, active in option_set.items() if active]
        if len(active_options) > 1:
            raise typer.BadParameter(
                f"Cannot combine {', '.join(active_options)}.",
                param_hint=active_options[0],
            )

    if agent is not None:
        agent_file = DEFAULT_AGENT_FILE

    ui: UIMode = "shell"
    if print_mode:
        ui = "print"
    elif acp_mode:
        ui = "acp"
    elif wire_mode:
        ui = "wire"

    if prompt is not None:
        prompt = prompt.strip()
        if not prompt:
            raise typer.BadParameter("Prompt cannot be empty", param_hint="--prompt")

    if input_format is not None and ui != "print":
        raise typer.BadParameter(
            "Input format is only supported for print UI",
            param_hint="--input-format",
        )
    if output_format is not None and ui != "print":
        raise typer.BadParameter(
            "Output format is only supported for print UI",
            param_hint="--output-format",
        )
    if final_message_only and ui != "print":
        raise typer.BadParameter(
            "Final-message-only output is only supported for print UI",
            param_hint="--final-message-only",
        )
    if _picker_mode and ui != "shell":
        raise typer.BadParameter(
            "--session without a session ID is only supported for shell UI",
            param_hint="--session",
        )

    config: Config | Path | None = None
    if config_string is not None:
        config_string = config_string.strip()
        if not config_string:
            raise typer.BadParameter("Config cannot be empty", param_hint="--config")
        try:
            config = load_config_from_string(config_string)
        except ConfigError as e:
            raise typer.BadParameter(str(e), param_hint="--config") from e
    elif config_file is not None:
        config = config_file

    file_configs = list(mcp_config_file or [])
    raw_mcp_config = list(mcp_config or [])

    # 如果未提供 MCP 配置则使用默认 MCP 配置文件
    if not file_configs:
        default_mcp_file = get_global_mcp_config_file()
        if default_mcp_file.exists():
            file_configs.append(default_mcp_file)

    try:
        mcp_configs = [json.loads(conf.read_text(encoding="utf-8")) for conf in file_configs]
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"Invalid JSON: {e}", param_hint="--mcp-config-file") from e

    try:
        mcp_configs += [json.loads(conf) for conf in raw_mcp_config]
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"Invalid JSON: {e}", param_hint="--mcp-config") from e

    skills_dirs: list[KaosPath] | None = None
    if local_skills_dir:
        skills_dirs = [KaosPath.unsafe_from_local_path(p) for p in local_skills_dir]

    work_dir = KaosPath.unsafe_from_local_path(local_work_dir) if local_work_dir else KaosPath.cwd()

    # 跟踪最近创建/加载的会话，以便 _reload_loop 的
    # 异常处理程序即使 _run() 在返回前失败也能清理它。
    _latest_created_session: Session | None = None

    async def _run(session_id: str | None, prefill_text: str | None = None) -> tuple[Session, int]:
        """创建/加载会话并运行 CLI 实例。

        Args:
            session_id: 会话 ID。
            prefill_text: 预填充文本。

        Returns:
            元组 (session, exit_code)，exit_code 为 0=成功, 1=失败, 75=可重试。
        """
        nonlocal config
        startup_progress = ShellStartupProgress(enabled=ui == "shell")
        try:
            startup_progress.update("准备会话...")

            # 跟踪是否在恢复现有会话（相对于新建）
            resumed = False

            if session_id is not None:
                session = await Session.find(work_dir, session_id)
                if session is None:
                    logger.info(
                        "Session {session_id} not found, creating new session",
                        session_id=session_id,
                    )
                    session = await Session.create(work_dir, session_id)
                else:
                    # 只有当会话有实际 turns 时才计为"恢复"。
                    # 通过 Reload 由 /new、/undo (turn 0)、/fork 创建的会话
                    # 可能有 custom_title 但无 wire 内容 —— 作为启动处理。
                    resumed = not session.wire_file.is_empty()
                logger.info("Resuming session: {session_id}", session_id=session.id)
            elif continue_:
                session = await Session.continue_(work_dir)
                if session is None:
                    raise typer.BadParameter(
                        "No previous session found for the working directory",
                        param_hint="--continue",
                    )
                resumed = True  # 继续上一个会话
                logger.info("Continuing previous session: {session_id}", session_id=session.id)
            else:
                session = await Session.create(work_dir)
                logger.info("Created new session: {session_id}", session_id=session.id)

            nonlocal _latest_created_session
            _latest_created_session = session

            # 将 CLI 提供的额外目录添加到会话状态
            if local_add_dirs:
                from novel_cli.utils.path import is_within_directory

                canonical_work_dir = work_dir.canonical()
                changed = False
                for d in local_add_dirs:
                    dir_path = KaosPath.unsafe_from_local_path(d).canonical()
                    dir_str = str(dir_path)
                    # 跳过 work_dir 内的目录（已可访问）
                    if is_within_directory(dir_path, canonical_work_dir):
                        logger.info(
                            "Skipping --add-dir {dir}: already within working directory",
                            dir=dir_str,
                        )
                        continue
                    if dir_str not in session.state.additional_dirs:
                        session.state.additional_dirs.append(dir_str)
                        changed = True
                if changed:
                    session.save_state()

            # 在 NovelCLI.create() *之前* 重定向 stderr，这样 MCP server
            # 子进程（如 mcp-remote OAuth 调试日志）写入日志文件
            # 而不是污染用户的终端。CLI 参数
            # 解析已经成功，Typer/Click
            # 启动错误不再需要关注。来自
            # create() 的致命错误仍然可见，因为 _emit_fatal_error() 写入
            # 保存的原始 stderr fd。
            redirect_stderr_to_logger()

            # 首次运行自动引导：如果 shell 模式下无 provider/model 配置，
            # 交互式引导用户完成设置。
            config_to_check = config if isinstance(config, Config) else None
            if config_to_check is None:
                from novel_cli.config import load_config

                config_to_check = load_config(config if isinstance(config, Path) else None)

            if ui == "shell" and _is_first_run(config_to_check):
                from novel_cli.setup.wizard import run_wizard

                ok = await run_wizard()
                if ok:
                    from rich.console import Console

                    Console().print("\n[green]设置完成！正在加载...\n[/green]")
                    # 设置后重新加载配置
                    if isinstance(config, Path) or config is None:
                        from novel_cli.config import load_config

                        config = load_config(config)
                else:
                    from rich.console import Console

                    Console().print("[yellow]设置已取消。您可以稍后运行 'novel setup'。[/yellow]")

            instance = await NovelCLI.create(
                session,
                config=config,
                model_name=model_name,
                thinking=thinking,
                yolo=yolo or (ui == "print"),  # print 模式隐含 yolo
                plan_mode=plan,
                resumed=resumed,
                agent_file=agent_file,
                mcp_configs=mcp_configs,
                skills_dirs=skills_dirs,
                max_steps_per_turn=max_steps_per_turn,
                max_retries_per_step=max_retries_per_step,
                max_ralph_iterations=max_ralph_iterations,
                startup_progress=startup_progress.update if ui == "shell" else None,
                defer_mcp_loading=ui == "shell" and prompt is None,
                book_name=book,
            )
            startup_progress.stop()

            # --- SessionStart hook ---
            _session_source = "resume" if resumed else "startup"
            await instance.soul.hook_engine.trigger(
                "SessionStart",
                matcher_value=_session_source,
                input_data=hook_events.session_start(
                    session_id=session.id,
                    cwd=str(work_dir),
                    source=_session_source,
                ),
            )

            # 仅在初始化成功后安装 stderr 重定向，这样运行时
            # stderr 噪声被记录到日志而不会隐藏启动失败。
            redirect_stderr_to_logger()
            preserve_background_tasks = False
            try:
                match ui:
                    case "shell":
                        shell_ok = await instance.run_shell(prompt, prefill_text=prefill_text)
                        exit_code = ExitCode.SUCCESS if shell_ok else ExitCode.FAILURE
                    case "print":
                        exit_code = await instance.run_print(
                            input_format or "text",
                            output_format or "text",
                            prompt,
                            final_only=final_message_only,
                        )
                    case "acp":
                        if prompt is not None:
                            logger.warning("ACP server ignores prompt argument")
                        await instance.run_acp()
                        exit_code = ExitCode.SUCCESS
                    case "wire":
                        if prompt is not None:
                            logger.warning("Wire server ignores prompt argument")
                        await instance.run_wire_stdio()
                        exit_code = ExitCode.SUCCESS
            except Reload as e:
                preserve_background_tasks = True
                if e.session_id is None:
                    r = Reload(session_id=session.id, prefill_text=e.prefill_text)
                    r.source_session = session
                    raise r from e
                e.source_session = session
                raise
            except SwitchToWeb:
                preserve_background_tasks = True
                raise
            except SwitchToVis:
                preserve_background_tasks = True
                raise
            finally:
                # --- SessionEnd hook ---
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        instance.soul.hook_engine.trigger(
                            "SessionEnd",
                            matcher_value="exit",
                            input_data=hook_events.session_end(
                                session_id=session.id,
                                cwd=str(work_dir),
                                reason="exit",
                            ),
                        ),
                        timeout=5,
                    )

                if not preserve_background_tasks:
                    instance.shutdown_background_tasks()

            return session, exit_code
        finally:
            startup_progress.stop()

    async def _delete_empty_session(session: Session) -> None:
        """删除空会话目录并清除指向它的 last_session_id。"""
        logger.info(
            "Session {session_id} has empty context, removing it",
            session_id=session.id,
        )
        await session.delete()
        meta = load_metadata()
        wdm = meta.get_work_dir_meta(session.work_dir)
        if wdm is not None and wdm.last_session_id == session.id:
            wdm.last_session_id = None
            save_metadata(meta)

    def _print_resume_hint(session: Session) -> None:
        """打印退出后恢复会话的提示。"""
        if not session.is_empty():
            _emit_fatal_error(f"\nTo resume this session: novel -r {session.id}")

    async def _post_run(last_session: Session, exit_code: int) -> None:
        _print_resume_hint(last_session)
        if last_session.is_empty():
            # 无论退出码如何，始终清理空会话
            await _delete_empty_session(last_session)
        elif exit_code == ExitCode.SUCCESS:
            metadata = load_metadata()
            work_dir_meta = metadata.get_work_dir_meta(last_session.work_dir)
            if work_dir_meta is None:
                logger.warning(
                    "Work dir metadata missing when marking last session, recreating: {work_dir}",
                    work_dir=last_session.work_dir,
                )
                work_dir_meta = metadata.new_work_dir_meta(last_session.work_dir)
            work_dir_meta.last_session_id = last_session.id
            save_metadata(metadata)

    async def _reload_loop(session_id: str | None) -> tuple[str | None, int]:
        """运行主循环，处理 Reload/SwitchToWeb/SwitchToVis。

        Args:
            session_id: 初始会话 ID。

        Returns:
            元组 (switch_target, exit_code)，switch_target 为 "web"、"vis"
            或 None（表示会话正常结束）。
        """
        last_session: Session | None = None
        prefill_text: str | None = None
        try:
            while True:
                try:
                    last_session, exit_code = await _run(session_id, prefill_text=prefill_text)
                    break
                except Reload as e:
                    # 切换到不同会话时清理旧的空会话
                    old = e.source_session
                    if old is not None and old.id != e.session_id and old.is_empty():
                        await _delete_empty_session(old)
                        last_session = None
                    else:
                        last_session = e.source_session
                        # 仅在切换到不同会话时打印恢复提示
                        #（不适用于同会话 reload 如 /model、/theme、/reload）
                        if old is not None and e.session_id is not None and old.id != e.session_id:
                            _print_resume_hint(old)
                    session_id = e.session_id
                    prefill_text = e.prefill_text
                    continue
                except SwitchToWeb as e:
                    if e.session_id is not None:
                        session = await Session.find(work_dir, e.session_id)
                        if session is not None:
                            await _post_run(session, ExitCode.SUCCESS)
                    return "web", ExitCode.SUCCESS
                except SwitchToVis as e:
                    if e.session_id is not None:
                        session = await Session.find(work_dir, e.session_id)
                        if session is not None:
                            await _post_run(session, ExitCode.SUCCESS)
                    return "vis", ExitCode.SUCCESS
            assert last_session is not None
            await _post_run(last_session, exit_code)
            return None, exit_code
        except (SwitchToWeb, SwitchToVis):
            # 当前在循环内处理（返回），但显式重新抛出
            # 以便下面的通用 except 永不会将它们视为意外错误。
            raise
        except Exception:
            # 尽力清理：_latest_created_session 是来自
            # 最近 _run() 调用的会话，可能在返回前失败。
            # last_session 来自*前一次*迭代，不应被触碰。
            if _latest_created_session is not None:
                _print_resume_hint(_latest_created_session)
                if _latest_created_session.is_empty():
                    with contextlib.suppress(Exception):
                        await _delete_empty_session(_latest_created_session)
            raise

    if _picker_mode:
        from prompt_toolkit.shortcuts.choice_input import ChoiceInput
        from rich.console import Console

        from novel_cli.utils.datetime import format_relative_time

        async def _pick_session() -> str:
            all_sessions = await Session.list(work_dir)
            if not all_sessions:
                Console().print("[yellow]未找到工作目录的会话。[/yellow]")
                raise typer.Exit(0)

            choices: list[tuple[str, str]] = []
            for s in all_sessions:
                time_str = format_relative_time(s.updated_at)
                short_id = s.id[:8]
                name = _strip_session_id_suffix(s.title, s.id)
                label = f"{name} ({short_id}), {time_str}"
                choices.append((s.id, label))

            try:
                selection = await ChoiceInput(
                    message="选择要恢复的会话"
                    "（↑↓ 导航，Enter 选择，Ctrl+C 取消）：",
                    options=choices,
                    default=choices[0][0],
                ).prompt_async()
            except (EOFError, KeyboardInterrupt):
                raise typer.Exit(0) from None

            if not selection:
                raise typer.Exit(0)

            return selection

        session_id = asyncio.run(_pick_session())

    try:
        switch_target, exit_code = asyncio.run(_reload_loop(session_id))
    except (typer.BadParameter, typer.Exit):
        # 让 Typer/Click 格式化这些错误（rich panel + 正确退出码）。
        raise
    except Exception as exc:
        import click

        if isinstance(exc, click.ClickException):
            # ClickException 包括 Typer 知道如何渲染的错误；不要
            # 包装它们，否则会丢失标准错误 UI 和退出码。
            raise
        logger.exception("运行 CLI 时发生致命错误")
        if debug:
            import traceback

            # 在调试模式下，显示完整堆栈跟踪以便快速诊断。
            _emit_fatal_error(traceback.format_exc())
        else:
            from novel_cli.share import get_share_dir

            log_path = get_share_dir() / "logs" / "novel.log"
            # 在非调试模式下，打印简洁错误并引导用户查看日志。
            _emit_fatal_error(f"{exc}\n查看日志：{log_path}")
        raise typer.Exit(code=1) from exc
    if switch_target in ("web", "vis"):
        from novel_cli.utils.logging import restore_stderr

        restore_stderr()

        # 在 shell 的 asyncio.run() 后恢复默认 SIGINT 处理器和终端状态
        # 以确保 Ctrl+C 在 uvicorn web server 中工作。
        import signal

        signal.signal(signal.SIGINT, signal.default_int_handler)

        from novel_cli.utils.term import ensure_tty_sane

        ensure_tty_sane()

        if switch_target == "web":
            from novel_cli.web.app import run_web_server

            run_web_server(open_browser=True)
        else:
            from novel_cli.vis.app import run_vis_server

            run_vis_server(open_browser=True)
    elif exit_code != ExitCode.SUCCESS:
        raise typer.Exit(code=exit_code)


@cli.command()
def setup(
    section: Annotated[
        str | None,
        typer.Argument(help="配置模块: provider|model|prefs|services|wizard"),
    ] = None,
) -> None:
    """交互式配置管理。"""
    import asyncio

    from novel_cli.setup import run_setup

    asyncio.run(run_setup(section))


@cli.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def term(
    ctx: typer.Context,
) -> None:
    """运行 Toad TUI，后台由 Novel CLI ACP server 支持。

    Args:
        ctx: Typer 上下文对象。
    """
    from .toad import run_term

    run_term(ctx)


@cli.command()
def acp():
    """运行 Novel CLI ACP server。"""
    from novel_cli.acp import acp_main

    acp_main()


@cli.command(name="__background-task-worker", hidden=True)
def background_task_worker(
    task_dir: Annotated[Path, typer.Option("--task-dir")],
    heartbeat_interval_ms: Annotated[int, typer.Option("--heartbeat-interval-ms")] = 5000,
    control_poll_interval_ms: Annotated[int, typer.Option("--control-poll-interval-ms")] = 500,
    kill_grace_period_ms: Annotated[int, typer.Option("--kill-grace-period-ms")] = 2000,
) -> None:
    """运行后台任务 worker 子进程（内部）。

    Args:
        task_dir: 任务目录路径。
        heartbeat_interval_ms: 心跳间隔（毫秒）。
        control_poll_interval_ms: 控制轮询间隔（毫秒）。
        kill_grace_period_ms: 终止宽限期（毫秒）。
    """
    import asyncio

    from novel_cli.background import run_background_task_worker
    from novel_cli.utils.proctitle import set_process_title

    set_process_title("novel-code-bg-worker")

    from novel_cli.app import enable_logging

    enable_logging(debug=False)
    asyncio.run(
        run_background_task_worker(
            task_dir,
            heartbeat_interval_ms=heartbeat_interval_ms,
            control_poll_interval_ms=control_poll_interval_ms,
            kill_grace_period_ms=kill_grace_period_ms,
        )
    )


@cli.command(name="__web-worker", hidden=True)
def web_worker(session_id: str) -> None:
    """运行 web worker 子进程（内部）。

    Args:
        session_id: 会话 ID。

    Raises:
        typer.BadParameter: 当会话 ID 无效时。
    """
    import asyncio
    from uuid import UUID

    from novel_cli.utils.proctitle import set_process_title

    set_process_title("novel-code-worker")

    from novel_cli.app import enable_logging
    from novel_cli.web.runner.worker import run_worker

    try:
        parsed_session_id = UUID(session_id)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid session ID: {session_id}") from exc

    enable_logging(debug=False)
    asyncio.run(run_worker(parsed_session_id))


if __name__ == "__main__":
    import sys

    if "novel_cli.cli" not in sys.modules:
        sys.modules["novel_cli.cli"] = sys.modules[__name__]

    sys.exit(cli())
