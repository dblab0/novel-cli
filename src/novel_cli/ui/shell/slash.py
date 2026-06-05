"""斜杠命令模块：提供 Shell 级斜杠命令的实现。

本模块定义了在 Shell 级别可用的斜杠命令，包括：
- 基础命令：exit、help、version
- 模型配置：model（切换 LLM 模型或思考模式）
- 编辑器配置：editor（设置默认外部编辑器）
- 会话管理：clear、new、title、sessions
- 任务管理：task（后台任务浏览器）
- 主题切换：theme
- 系统功能：web、vis、mcp、hooks
- 会话操作：undo、fork、changelog、feedback

所有命令都注册到 registry 和 shell_mode_registry 中。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from prompt_toolkit.shortcuts.choice_input import ChoiceInput

from novel_cli import logger
from novel_cli.auth.platforms import get_platform_name_for_provider, refresh_managed_models
from novel_cli.cli import Reload, SwitchToVis, SwitchToWeb
from novel_cli.config import load_config, save_config
from novel_cli.exception import ConfigError
from novel_cli.session import Session
from novel_cli.soul.novelsoul import NovelSoul
from novel_cli.store import NovelStore
from novel_cli.ui.shell.console import console
from novel_cli.ui.shell.mcp_status import render_mcp_console
from novel_cli.ui.shell.task_browser import TaskBrowserApp
from novel_cli.utils.changelog import CHANGELOG
from novel_cli.utils.datetime import format_relative_time
from novel_cli.utils.slashcmd import SlashCommand, SlashCommandRegistry

if TYPE_CHECKING:
    from novel_cli.ui.shell import Shell

type ShellSlashCmdFunc = Callable[[Shell, str], None | Awaitable[None]]
"""Shell 级斜杠命令函数类型。

Args:
    app: Shell 实例。
    args: 命令参数字符串。

Returns:
    无返回值或协程。

Raises:
    Reload: 当需要重新加载配置时。
"""


registry = SlashCommandRegistry[ShellSlashCmdFunc]()
"""Shell 级斜杠命令注册表（智能体模式）。"""

shell_mode_registry = SlashCommandRegistry[ShellSlashCmdFunc]()
"""Shell 级斜杠命令注册表（Shell 模式）。"""


def ensure_novel_soul(app: Shell) -> NovelSoul | None:
    """确保智能体是 NovelSoul 类型。

    Args:
        app: Shell 实例。

    Returns:
        如果智能体是 NovelSoul 则返回其实例，否则返回 None。
    """
    if not isinstance(app.soul, NovelSoul):
        console.print("[red]需要 NovelSoul[/red]")
        return None
    return app.soul


@registry.command(aliases=["quit"])
@shell_mode_registry.command(aliases=["quit"])
def exit(app: Shell, args: str):
    """退出应用程序。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        NotImplementedError: 此命令应由 Shell 处理。
    """
    # 应由 Shell 处理
    raise NotImplementedError


SKILL_COMMAND_PREFIX = "skill:"
"""技能命令前缀。"""

_KEYBOARD_SHORTCUTS = [
    ("Ctrl-X", "切换智能体/Shell 模式"),
    ("Shift-Tab", "切换规划模式（只读研究）"),
    ("Ctrl-O", "在外部编辑器中编辑 ($VISUAL/$EDITOR)"),
    ("Ctrl-J / Alt-Enter", "插入换行"),
    ("Ctrl-V", "粘贴（支持图片）"),
    ("Ctrl-D", "退出"),
    ("Ctrl-C", "中断"),
]
"""键盘快捷键列表。"""


@registry.command(aliases=["h", "?"])
@shell_mode_registry.command(aliases=["h", "?"])
def help(app: Shell, args: str):
    """显示帮助信息。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。
    """
    from rich.console import Group, RenderableType
    from rich.text import Text

    from novel_cli.utils.rich.columns import BulletColumns

    def section(title: str, items: list[tuple[str, str]], color: str) -> BulletColumns:
        """创建帮助信息的一个章节。

        Args:
            title: 章节标题。
            items: 条目列表，每项为 (名称, 描述)。
            color: 显示颜色。

        Returns:
            带项目符号的章节渲染对象。
        """
        lines: list[RenderableType] = [Text.from_markup(f"[bold]{title}:[/bold]")]
        for name, desc in items:
            lines.append(
                BulletColumns(
                    Text.from_markup(f"[{color}]{name}[/{color}]: [grey50]{desc}[/grey50]"),
                    bullet_style=color,
                )
            )
        return BulletColumns(Group(*lines))

    renderables: list[RenderableType] = []
    renderables.append(
        BulletColumns(
            Group(
                Text.from_markup("[grey50]Help! I need somebody. Help! Not just anybody.[/grey50]"),
                Text.from_markup("[grey50]Help! You know I need someone. Help![/grey50]"),
                Text.from_markup("[grey50]\u2015 The Beatles, [italic]Help![/italic][/grey50]"),
            ),
            bullet_style="grey50",
        )
    )
    renderables.append(
        BulletColumns(
            Text(
                "当然，Novel 随时准备为您提供帮助！"
                "只需发送消息，我将帮助您完成任务！"
            ),
        )
    )

    commands: list[SlashCommand[Any]] = []
    skills: list[SlashCommand[Any]] = []
    for cmd in app.available_slash_commands.values():
        if cmd.name.startswith(SKILL_COMMAND_PREFIX):
            skills.append(cmd)
        else:
            commands.append(cmd)

    renderables.append(section("键盘快捷键", _KEYBOARD_SHORTCUTS, "yellow"))
    renderables.append(
        section(
            "斜杠命令",
            [(c.slash_name(), c.description) for c in sorted(commands, key=lambda c: c.name)],
            "blue",
        )
    )
    if skills:
        renderables.append(
            section(
                "技能",
                [(c.slash_name(), c.description) for c in sorted(skills, key=lambda c: c.name)],
                "cyan",
            )
        )

    with console.pager(styles=True):
        console.print(Group(*renderables))


@registry.command
@shell_mode_registry.command
def version(app: Shell, args: str):
    """显示版本信息。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。
    """
    from novel_cli.constant import VERSION

    console.print(f"novel, 版本 {VERSION}")


@registry.command
async def model(app: Shell, args: str):
    """切换 LLM 模型或思考模式。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        Reload: 切换成功后重新加载会话。
    """
    from novel_cli.llm import derive_model_capabilities

    soul = ensure_novel_soul(app)
    if soul is None:
        return
    config = soul.runtime.config

    await refresh_managed_models(config)

    if not config.models:
        console.print("[yellow]未配置模型，请运行 'novel setup' 进行配置。[/yellow]")
        return

    if not config.is_from_default_location:
        console.print(
            "[yellow]模型切换需要默认配置文件；"
            "请不带 --config/--config-file 重启。[/yellow]"
        )
        return

    # 从运行时查找当前模型/思考模式（可能被 --model/--thinking 覆盖）
    curr_model_cfg = soul.runtime.llm.model_config if soul.runtime.llm else None
    curr_model_name: str | None = None
    if curr_model_cfg is not None:
        for name, model_cfg in config.models.items():
            if model_cfg == curr_model_cfg:
                curr_model_name = name
                break
    curr_thinking = soul.thinking

    # 步骤 1：选择模型
    model_choices: list[tuple[str, str]] = []
    for name in sorted(config.models):
        model_cfg = config.models[name]
        provider_label = get_platform_name_for_provider(model_cfg.provider) or model_cfg.provider
        marker = " (当前)" if name == curr_model_name else ""
        label = f"{model_cfg.model} ({provider_label}){marker}"
        model_choices.append((name, label))

    try:
        selected_model_name = await ChoiceInput(
            message="选择模型 (↑↓ 导航, Enter 选择, Ctrl+C 取消):",
            options=model_choices,
            default=curr_model_name or model_choices[0][0],
        ).prompt_async()
    except (EOFError, KeyboardInterrupt):
        return

    if not selected_model_name:
        return

    selected_model_cfg = config.models[selected_model_name]
    selected_provider = config.providers.get(selected_model_cfg.provider)
    if selected_provider is None:
        console.print(f"[red]未找到提供者: {selected_model_cfg.provider}[/red]")
        return

    # 步骤 2：确定思考模式
    capabilities = derive_model_capabilities(selected_model_cfg)
    new_thinking: bool

    if "always_thinking" in capabilities:
        new_thinking = True
    elif "thinking" in capabilities:
        thinking_choices: list[tuple[str, str]] = [
            ("off", "关闭" + (" (当前)" if not curr_thinking else "")),
            ("on", "开启" + (" (当前)" if curr_thinking else "")),
        ]
        try:
            thinking_selection = await ChoiceInput(
                message="启用思考模式? (↑↓ 导航, Enter 选择, Ctrl+C 取消):",
                options=thinking_choices,
                default="on" if curr_thinking else "off",
            ).prompt_async()
        except (EOFError, KeyboardInterrupt):
            return

        if not thinking_selection:
            return

        new_thinking = thinking_selection == "on"
    else:
        new_thinking = False

    # 检查是否有变化
    model_changed = curr_model_name != selected_model_name
    thinking_changed = curr_thinking != new_thinking

    if not model_changed and not thinking_changed:
        console.print(
            f"[yellow]已在使用 {selected_model_name}，"
            f"思考模式 {'开启' if new_thinking else '关闭'}。[/yellow]"
        )
        return

    # 保存并重新加载
    prev_model = config.default_model
    prev_thinking = config.default_thinking
    config.default_model = selected_model_name
    config.default_thinking = new_thinking
    try:
        config_for_save = load_config()
        config_for_save.default_model = selected_model_name
        config_for_save.default_thinking = new_thinking
        save_config(config_for_save)
    except (ConfigError, OSError) as exc:
        config.default_model = prev_model
        config.default_thinking = prev_thinking
        console.print(f"[red]保存配置失败: {exc}[/red]")
        return

    console.print(
        f"[green]已切换到 {selected_model_name}，"
        f"思考模式 {'开启' if new_thinking else '关闭'}。"
        "正在重新加载...[/green]"
    )
    raise Reload(session_id=soul.runtime.session.id)


@registry.command
async def book(app: Shell, args: str):
    """选择当前查询的书籍。

    支持三种模式：
    - /book 书名：精确匹配或关键词搜索后选择
    - /book：显示全部书籍供选择
    - /book none：清除当前选书

    Args:
        app: Shell 实例。
        args: 书名或关键词参数。

    Raises:
        Reload: 选书后需要重新加载会话状态。
    """
    from novel_cli.session_state import load_session_state, save_session_state

    soul = ensure_novel_soul(app)
    if soul is None:
        return

    config = soul.runtime.config
    novel_db = config.services.novel_db

    # 数据库未配置时提示
    if not novel_db.pg_password.get_secret_value():
        console.print("[yellow]小说知识库未配置，请配置数据库连接[/yellow]")
        return

    # 创建 NovelStore 并获取书籍列表
    store = NovelStore(novel_db)
    try:
        await store.connect()
        books = await store.list_books()
    finally:
        await store.close()

    # 处理参数
    args = args.strip()

    # /book none - 清除选书
    if args.lower() == "none":
        session = soul.runtime.session
        fresh = load_session_state(session.dir)
        fresh.current_book = None
        save_session_state(fresh, session.dir)
        session.state.current_book = None
        console.print("[green]已清除当前选书[/green]")
        return

    # 精确匹配检查
    if args:
        exact_match = [b for b in books if b == args]
        if exact_match:
            # 直接选中
            book_name = exact_match[0]
            session = soul.runtime.session
            fresh = load_session_state(session.dir)
            fresh.current_book = book_name
            save_session_state(fresh, session.dir)
            session.state.current_book = book_name
            console.print(f"[green]已选择书籍: {book_name}[/green]")
            return

        # 关键词搜索
        keyword = args.lower()
        matches = [b for b in books if keyword in b.lower()]
        if not matches:
            console.print(f"[yellow]未找到匹配书籍: {args}[/yellow]")
            return
    else:
        # 无参数，显示全部
        matches = books

    # ChoiceInput 选择
    current_book = soul.runtime.session.state.current_book
    choices: list[tuple[str, str]] = []
    for b in matches:
        marker = " (当前)" if b == current_book else ""
        choices.append((b, f"{b}{marker}"))

    try:
        selected = await ChoiceInput(
            message="选择书籍 (↑↓ 导航, Enter 选择, Ctrl+C 取消):",
            options=choices,
            default=current_book if current_book in matches else matches[0],
        ).prompt_async()
    except (EOFError, KeyboardInterrupt):
        return

    if not selected:
        return

    # 更新 session state
    session = soul.runtime.session
    fresh = load_session_state(session.dir)
    fresh.current_book = selected
    save_session_state(fresh, session.dir)
    session.state.current_book = selected
    console.print(f"[green]已选择书籍: {selected}[/green]")


@registry.command
@shell_mode_registry.command
async def editor(app: Shell, args: str):
    """设置 Ctrl-O 使用的默认外部编辑器。

    Args:
        app: Shell 实例。
        args: 编辑器命令字符串，可选。
    """
    from novel_cli.utils.editor import get_editor_command

    soul = ensure_novel_soul(app)
    if soul is None:
        return
    config = soul.runtime.config
    config_file = config.source_file
    if config_file is None:
        console.print(
            "[yellow]使用内联 --config 时无法切换编辑器；"
            "请使用 --config-file 保存此设置。[/yellow]"
        )
        return

    current_editor = config.default_editor

    # 如果直接提供了参数，用作编辑器命令
    if args.strip():
        new_editor = args.strip()
    else:
        options: list[tuple[str, str]] = [
            ("code --wait", "VS Code (code --wait)"),
            ("vim", "Vim"),
            ("nano", "Nano"),
            ("", "自动检测 (使用 $VISUAL/$EDITOR)"),
        ]
        # 标记当前选择
        options = [
            (val, label + (" ← 当前" if val == current_editor else "")) for val, label in options
        ]

        try:
            choice = cast(
                str | None,
                await ChoiceInput(
                    message="选择编辑器 (↑↓ 导航, Enter 选择, Ctrl+C 取消):",
                    options=options,
                    default=(
                        current_editor
                        if current_editor in {v for v, _ in options}
                        else "code --wait"
                    ),
                ).prompt_async(),
            )
        except (EOFError, KeyboardInterrupt):
            return

        if choice is None:
            return
        new_editor = choice

    # 验证编辑器二进制文件是否可用
    if new_editor:
        import shlex
        import shutil

        try:
            parts = shlex.split(new_editor)
        except ValueError:
            console.print(f"[red]无效的编辑器命令: {new_editor}[/red]")
            return

        binary = parts[0]
        if not shutil.which(binary):
            console.print(
                f"[yellow]警告: '{binary}' 在 PATH 中未找到。"
                f"仍然保存 —— 请在使用 Ctrl-O 前确保已安装。[/yellow]"
            )

    if new_editor == current_editor:
        console.print(f"[yellow]编辑器已设置为: {new_editor or '自动检测'}[/yellow]")
        return

    # 保存到磁盘
    try:
        config_for_save = load_config(config_file)
        config_for_save.default_editor = new_editor
        save_config(config_for_save, config_file)
    except (ConfigError, OSError) as exc:
        console.print(f"[red]保存配置失败: {exc}[/red]")
        return

    # 同步内存配置以便 Ctrl-O 立即生效
    config.default_editor = new_editor

    if new_editor:
        console.print(f"[green]编辑器设置为: {new_editor}[/green]")
    else:
        resolved = get_editor_command()
        label = " ".join(resolved) if resolved else "无"
        console.print(f"[green]编辑器设置为自动检测 (解析结果: {label})[/green]")


@registry.command(aliases=["release-notes"])
@shell_mode_registry.command(aliases=["release-notes"])
def changelog(app: Shell, args: str):
    """显示更新日志。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。
    """
    from rich.console import Group, RenderableType
    from rich.text import Text

    from novel_cli.utils.rich.columns import BulletColumns

    renderables: list[RenderableType] = []
    for ver, entry in CHANGELOG.items():
        title = f"[bold]{ver}[/bold]"
        if entry.description:
            title += f": {entry.description}"

        lines: list[RenderableType] = [Text.from_markup(title)]
        for item in entry.entries:
            if item.lower().startswith("lib:"):
                continue
            lines.append(
                BulletColumns(
                    Text.from_markup(f"[grey50]{item}[/grey50]"),
                    bullet_style="grey50",
                ),
            )
        renderables.append(BulletColumns(Group(*lines)))

    with console.pager(styles=True):
        console.print(Group(*renderables))


@registry.command
@shell_mode_registry.command
async def feedback(app: Shell, args: str):
    """提交反馈以帮助改进 Novel CLI。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。
    """
    import platform
    import webbrowser

    import aiohttp

    from novel_cli.auth import NOVEL_CODE_PLATFORM_ID
    from novel_cli.auth.platforms import get_platform_by_id, managed_provider_key
    from novel_cli.constant import VERSION
    from novel_cli.utils.aiohttp import new_client_session

    ISSUE_URL = "https://github.com/MoonshotAI/novel-cli/issues"

    def _fallback_to_issues():
        """回退到 GitHub Issues 页面。"""
        if not webbrowser.open(ISSUE_URL):
            console.print(f"请在 [underline]{ISSUE_URL}[/underline] 提交反馈。")

    soul = ensure_novel_soul(app)
    if soul is None:
        _fallback_to_issues()
        return

    novel_platform = get_platform_by_id(NOVEL_CODE_PLATFORM_ID)
    if novel_platform is None:
        _fallback_to_issues()
        return

    provider = soul.runtime.config.providers.get(managed_provider_key(NOVEL_CODE_PLATFORM_ID))
    if provider is None:
        _fallback_to_issues()
        return

    from prompt_toolkit import PromptSession

    prompt_session: PromptSession[str] = PromptSession()
    try:
        content = await prompt_session.prompt_async("输入您的反馈: ")
    except (EOFError, KeyboardInterrupt):
        console.print("[grey50]反馈已取消。[/grey50]")
        return

    content = content.strip()
    if not content:
        console.print("[yellow]反馈不能为空。[/yellow]")
        return

    api_key = provider.api_key.get_secret_value()
    feedback_url = f"{novel_platform.base_url.rstrip('/')}/feedback"

    payload = {
        "session_id": soul.runtime.session.id,
        "content": content,
        "version": VERSION,
        "os": f"{platform.system()} {platform.release()}",
        "model": soul.model_name,
    }

    with console.status("[cyan]正在提交反馈...[/cyan]"):
        try:
            async with (
                new_client_session() as session,
                session.post(
                    feedback_url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        **(provider.custom_headers or {}),
                    },
                    raise_for_status=True,
                ),
            ):
                pass
            session_id = soul.runtime.session.id
            console.print(
                f"[green]反馈已提交，感谢！您的会话 ID: {session_id}[/green]"
            )
        except TimeoutError:
            console.print("[red]反馈提交超时。[/red]")
            _fallback_to_issues()
        except aiohttp.ClientError as e:
            status = getattr(e, "status", None)
            if status:
                msg = f"反馈提交失败 (HTTP {status})。"
            else:
                msg = "网络错误，反馈提交失败。"
            console.print(f"[red]{msg}[/red]")
            _fallback_to_issues()


@registry.command(aliases=["reset"])
async def clear(app: Shell, args: str):
    """清除上下文。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        Reload: 清除后重新加载会话。
    """
    if ensure_novel_soul(app) is None:
        return
    await app.run_soul_command("/clear")
    raise Reload()


@registry.command
async def new(app: Shell, args: str):
    """开始新会话。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        Reload: 创建新会话后重新加载。
    """
    soul = ensure_novel_soul(app)
    if soul is None:
        return
    current_session = soul.runtime.session
    work_dir = current_session.work_dir
    # 如果当前会话没有内容，清理它，以便连续 /new 命令
    # （或在第一条消息前切换）不会在磁盘上留下孤立的空会话目录
    if current_session.is_empty():
        await current_session.delete()
    session = await Session.create(work_dir)
    console.print("[green]新会话已创建。正在切换...[/green]")
    raise Reload(session_id=session.id)


@registry.command(name="title", aliases=["rename"])
async def title(app: Shell, args: str):
    """设置或显示会话标题。

    Args:
        app: Shell 实例。
        args: 新标题字符串，如果为空则显示当前标题。
    """
    soul = ensure_novel_soul(app)
    if soul is None:
        return
    session = soul.runtime.session
    if not args.strip():
        console.print(f"会话标题: [bold]{session.title}[/bold]")
        return

    from novel_cli.session_state import load_session_state, save_session_state

    new_title = args.strip()[:200]
    # 读-改-写：加载最新状态以避免覆盖 Web 并发更改
    fresh = load_session_state(session.dir)
    fresh.custom_title = new_title
    fresh.title_generated = True
    save_session_state(fresh, session.dir)
    session.state.custom_title = new_title
    session.state.title_generated = True
    session.title = new_title
    console.print(f"[green]会话标题已设置为: {new_title}[/green]")


@registry.command(name="sessions", aliases=["resume"])
async def list_sessions(app: Shell, args: str):
    """列出会话并可选恢复。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        Reload: 选择会话后重新加载。
    """
    soul = ensure_novel_soul(app)
    if soul is None:
        return

    work_dir = soul.runtime.session.work_dir
    current_session = soul.runtime.session
    current_session_id = current_session.id
    sessions = [
        session for session in await Session.list(work_dir) if session.id != current_session_id
    ]

    await current_session.refresh()
    sessions.insert(0, current_session)

    choices: list[tuple[str, str]] = []
    for session in sessions:
        time_str = format_relative_time(session.updated_at)
        marker = " (当前)" if session.id == current_session_id else ""
        label = f"{session.title} ({session.id}), {time_str}{marker}"
        choices.append((session.id, label))

    try:
        selection = await ChoiceInput(
            message="选择要切换的会话 (↑↓ 导航, Enter 选择, Ctrl+C 取消):",
            options=choices,
            default=choices[0][0],
        ).prompt_async()
    except (EOFError, KeyboardInterrupt):
        return

    if not selection:
        return

    if selection == current_session_id:
        console.print("[yellow]您已在此会话中。[/yellow]")
        return

    console.print(f"[green]正在切换到会话 {selection}...[/green]")
    raise Reload(session_id=selection)


@registry.command(name="task")
@shell_mode_registry.command(name="task")
async def task(app: Shell, args: str):
    """浏览和管理后台任务。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。
    """
    soul = ensure_novel_soul(app)
    if soul is None:
        return
    if args.strip():
        console.print('[yellow]用法: "/task" 打开交互式任务浏览器。[/yellow]')
        return
    if soul.runtime.role != "root":
        console.print("[yellow]后台任务仅对根智能体可用。[/yellow]")
        return

    await TaskBrowserApp(soul).run()


@registry.command
@shell_mode_registry.command
def theme(app: Shell, args: str):
    """切换终端颜色主题（深色/浅色）。

    Args:
        app: Shell 实例。
        args: 主题名称（dark 或 light）。

    Raises:
        Reload: 切换成功后重新加载会话。
    """
    from novel_cli.ui.theme import get_active_theme

    soul = ensure_novel_soul(app)
    if soul is None:
        return

    current = get_active_theme()
    arg = args.strip().lower()

    if not arg:
        console.print(f"当前主题: [bold]{current}[/bold]")
        console.print("[grey50]用法: /theme dark | /theme light[/grey50]")
        return

    if arg not in ("dark", "light"):
        console.print(f"[red]未知主题: {arg}。请使用 'dark' 或 'light'。[/red]")
        return

    if arg == current:
        console.print(f"[yellow]已在使用 {arg} 主题。[/yellow]")
        return

    config_file = soul.runtime.config.source_file
    if config_file is None:
        console.print(
            "[yellow]主题切换需要配置文件；"
            "请不带 --config 重启以保存此设置。[/yellow]"
        )
        return

    # 先保存到磁盘 —— 只有成功后才更新内存状态
    try:
        config_for_save = load_config(config_file)
        config_for_save.theme = arg  # type: ignore[assignment]
        save_config(config_for_save, config_file)
    except (ConfigError, OSError) as exc:
        console.print(f"[red]保存配置失败: {exc}[/red]")
        return

    console.print(f"[green]已切换到 {arg} 主题。正在重新加载...[/green]")
    raise Reload(session_id=soul.runtime.session.id)


@registry.command
def web(app: Shell, args: str):
    """在浏览器中打开 Novel Code Web UI。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        SwitchToWeb: 触发切换到 Web UI。
    """
    soul = ensure_novel_soul(app)
    session_id = soul.runtime.session.id if soul else None
    raise SwitchToWeb(session_id=session_id)


@registry.command
def vis(app: Shell, args: str):
    """在浏览器中打开 Novel 智能体追踪可视化器。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        SwitchToVis: 触发切换到可视化器。
    """
    soul = ensure_novel_soul(app)
    session_id = soul.runtime.session.id if soul else None
    raise SwitchToVis(session_id=session_id)


@registry.command
async def mcp(app: Shell, args: str):
    """显示 MCP 服务器和工具。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。
    """
    from rich.live import Live

    soul = ensure_novel_soul(app)
    if soul is None:
        return
    await soul.start_background_mcp_loading()
    snapshot = soul.status.mcp_status
    if snapshot is None:
        console.print("[yellow]未配置 MCP 服务器。[/yellow]")
        return

    if not snapshot.loading:
        console.print(render_mcp_console(snapshot))
        return

    with Live(
        render_mcp_console(snapshot),
        console=console,
        refresh_per_second=8,
        transient=False,
    ) as live:
        while True:
            snapshot = soul.status.mcp_status
            if snapshot is None:
                break
            live.update(render_mcp_console(snapshot), refresh=True)
            if not snapshot.loading:
                break
            await asyncio.sleep(0.125)
        try:
            await soul.wait_for_background_mcp_loading()
        except Exception as e:
            logger.debug("渲染 /mcp 时 MCP 加载出错: {error}", error=e)
        snapshot = soul.status.mcp_status
        if snapshot is not None:
            live.update(render_mcp_console(snapshot), refresh=True)


@registry.command
@shell_mode_registry.command
def hooks(app: Shell, args: str):
    """列出配置的钩子。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。
    """
    soul = ensure_novel_soul(app)
    if soul is None:
        return

    engine = soul.hook_engine
    if not engine.summary:
        console.print(
            "[yellow]未配置钩子。"
            "在 config.toml 中添加 [[hooks]] 部分以设置钩子。[/yellow]"
        )
        return

    console.print()
    console.print("[bold]已配置的钩子:[/bold]")
    console.print()

    for event, entries in engine.details().items():
        console.print(f"  [cyan]{event}[/cyan]: {len(entries)} 个钩子")
        for entry in entries:
            source_tag = f" [dim]({entry['source']})[/dim]" if entry["source"] == "wire" else ""
            console.print(f"    [dim]{entry['matcher']}[/dim] {entry['command']}{source_tag}")

    console.print()


@registry.command
async def undo(app: Shell, args: str):
    """撤销：在之前的回合处分叉会话并重试。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        Reload: 分叉成功后重新加载新会话。
    """
    from novel_cli.session_fork import enumerate_turns, fork_session
    from novel_cli.utils.string import shorten

    soul = ensure_novel_soul(app)
    if soul is None:
        return

    session = soul.runtime.session
    wire_path = session.dir / "wire.jsonl"
    turns = enumerate_turns(wire_path)

    if not turns:
        console.print("[yellow]此会话中未找到回合。[/yellow]")
        return

    # 构建选项：每个回合的第一行，截断
    choices: list[tuple[str, str]] = []
    for turn in turns:
        first_line = turn.user_text.split("\n", 1)[0]
        label = shorten(first_line, width=80, placeholder="...")
        choices.append((str(turn.index), f"[{turn.index}] {label}"))

    try:
        selected = await ChoiceInput(
            message="选择要撤销的回合 (↑↓ 导航, Enter 选择, Ctrl+C 取消):",
            options=choices,
            default=choices[-1][0],
        ).prompt_async()
    except (EOFError, KeyboardInterrupt):
        return

    turn_index = int(selected)

    # 选中的回合是我们想要重做的 —— 分叉包含其之前的回合
    selected_turn = turns[turn_index]
    user_text = selected_turn.user_text

    if turn_index == 0:
        # 无历史分叉 —— 仅包含用户文本
        new_session = await Session.create(session.work_dir)
        new_session_id = new_session.id
        # 设置标题以符合 fork_session 的约定
        from novel_cli.session_state import load_session_state, save_session_state

        new_state = load_session_state(new_session.dir)
        new_state.custom_title = f"撤销: {session.title}"
        new_state.title_generated = True
        save_session_state(new_state, new_session.dir)
    else:
        # 分叉包含回合 0..turn_index-1
        fork_turn_index = turn_index - 1
        new_session_id = await fork_session(
            source_session_dir=session.dir,
            work_dir=session.work_dir,
            turn_index=fork_turn_index,
            title_prefix="撤销",
            source_title=session.title,
        )

    console.print(f"[green]已在回合 {turn_index} 处分叉。正在切换到新会话...[/green]")
    raise Reload(session_id=new_session_id, prefill_text=user_text)


@registry.command
async def fork(app: Shell, args: str):
    """分叉当前会话（将所有历史复制到新会话）。

    Args:
        app: Shell 实例。
        args: 命令参数（未使用）。

    Raises:
        Reload: 分叉成功后重新加载新会话。
    """
    from novel_cli.session_fork import fork_session

    soul = ensure_novel_soul(app)
    if soul is None:
        return

    session = soul.runtime.session
    new_session_id = await fork_session(
        source_session_dir=session.dir,
        work_dir=session.work_dir,
        turn_index=None,
        title_prefix="分叉",
        source_title=session.title,
    )

    console.print("[green]会话已分叉。正在切换到新会话...[/green]")
    raise Reload(session_id=new_session_id)


# 导入其他斜杠命令子模块
from . import (  # noqa: E402
    debug,  # noqa: F401 # type: ignore[reportUnusedImport]
    export_import,  # noqa: F401 # type: ignore[reportUnusedImport]
    update,  # noqa: F401 # type: ignore[reportUnusedImport]
    usage,  # noqa: F401 # type: ignore[reportUnusedImport]
)


@registry.command
def reload(app: Shell, args: str):  # noqa: ARG001
    """重载配置。"""
    raise Reload
