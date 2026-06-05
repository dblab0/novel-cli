"""soul 斜杠命令模块。

本模块定义了 NovelSoul 级别的斜杠命令，包括：
- /init: 分析代码库并生成 AGENTS.md 文件
- /compact: 压缩上下文
- /clear: 清空上下文
- /yolo: 切换自动批准模式
- /plan: 切换计划模式
- /add-dir: 添加目录到工作空间
- /export: 导出会话上下文
- /import: 导入上下文
- /book: 选择或浏览小说知识库书籍
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from kaos.path import KaosPath
from kosong.message import Message

import novel_cli.prompts as prompts
from novel_cli import logger
from novel_cli.soul import wire_send
from novel_cli.soul.agent import load_agents_md
from novel_cli.soul.context import Context
from novel_cli.soul.message import system
from novel_cli.utils.export import is_sensitive_file
from novel_cli.utils.path import sanitize_cli_path, shorten_home
from novel_cli.utils.slashcmd import SlashCommandRegistry
from novel_cli.wire.types import StatusUpdate, TextPart

if TYPE_CHECKING:
    from novel_cli.soul.novelsoul import NovelSoul

type SoulSlashCmdFunc = Callable[[NovelSoul, str], None | Awaitable[None]]
"""作为 NovelSoul 级别斜杠命令运行的函数。

Raises:
    Soul.run 可能抛出的任何异常。
"""

registry = SlashCommandRegistry[SoulSlashCmdFunc]()


@registry.command
async def init(soul: NovelSoul, args: str):
    """分析代码库并生成 AGENTS.md 文件。

    Args:
        soul: NovelSoul 实例。
        args: 命令参数（暂未使用）。
    """
    from novel_cli.soul.novelsoul import NovelSoul

    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_context = Context(file_backend=Path(temp_dir) / "context.jsonl")
        tmp_soul = NovelSoul(soul.agent, context=tmp_context)
        await tmp_soul.run(prompts.INIT)

    agents_md = await load_agents_md(soul.runtime.builtin_args.NOVEL_WORK_DIR)
    system_message = system(
        "The user just ran `/init` slash command. "
        "The system has analyzed the codebase and generated an `AGENTS.md` file. "
        f"Latest AGENTS.md file content:\n{agents_md}"
    )
    await soul.context.append_message(Message(role="user", content=[system_message]))


@registry.command
async def compact(soul: NovelSoul, args: str):
    """压缩上下文（可选指定自定义关注点，例如 /compact keep db discussions）。

    Args:
        soul: NovelSoul 实例。
        args: 可选的自定义关注指令。
    """
    if soul.context.n_checkpoints == 0:
        wire_send(TextPart(text="The context is empty."))
        return

    logger.info("Running `/compact`")
    await soul.compact_context(custom_instruction=args.strip())
    wire_send(TextPart(text="The context has been compacted."))
    snap = soul.status
    wire_send(
        StatusUpdate(
            context_usage=snap.context_usage,
            context_tokens=snap.context_tokens,
            max_context_tokens=snap.max_context_tokens,
        )
    )


@registry.command(aliases=["reset"])
async def clear(soul: NovelSoul, args: str):
    """清空上下文。

    Args:
        soul: NovelSoul 实例。
        args: 命令参数（暂未使用）。
    """
    logger.info("Running `/clear`")
    await soul.context.clear()
    await soul.context.write_system_prompt(soul.agent.system_prompt)
    wire_send(TextPart(text="The context has been cleared."))
    snap = soul.status
    wire_send(
        StatusUpdate(
            context_usage=snap.context_usage,
            context_tokens=snap.context_tokens,
            max_context_tokens=snap.max_context_tokens,
        )
    )


@registry.command
async def yolo(soul: NovelSoul, args: str):
    """切换 YOLO 模式（自动批准所有操作）。

    Args:
        soul: NovelSoul 实例。
        args: 命令参数（暂未使用）。
    """
    if soul.runtime.approval.is_yolo():
        soul.runtime.approval.set_yolo(False)
        wire_send(TextPart(text="You only die once! Actions will require approval."))
    else:
        soul.runtime.approval.set_yolo(True)
        wire_send(TextPart(text="You only live once! All actions will be auto-approved."))


@registry.command
async def plan(soul: NovelSoul, args: str):
    """切换计划模式。用法：/plan [on|off|view|clear]。

    Args:
        soul: NovelSoul 实例。
        args: 子命令参数（on/off/view/clear）。
    """
    subcmd = args.strip().lower()

    if subcmd == "on":
        if not soul.plan_mode:
            await soul.toggle_plan_mode_from_manual()
        plan_path = soul.get_plan_file_path()
        wire_send(TextPart(text=f"Plan mode ON. Plan file: {plan_path}"))
        wire_send(StatusUpdate(plan_mode=soul.plan_mode))
    elif subcmd == "off":
        if soul.plan_mode:
            await soul.toggle_plan_mode_from_manual()
        wire_send(TextPart(text="Plan mode OFF. All tools are now available."))
        wire_send(StatusUpdate(plan_mode=soul.plan_mode))
    elif subcmd == "view":
        content = soul.read_current_plan()
        if content:
            wire_send(TextPart(text=content))
        else:
            wire_send(TextPart(text="No plan file found for this session."))
    elif subcmd == "clear":
        soul.clear_current_plan()
        wire_send(TextPart(text="Plan cleared."))
    else:
        # 默认行为：切换状态
        new_state = await soul.toggle_plan_mode_from_manual()
        if new_state:
            plan_path = soul.get_plan_file_path()
            wire_send(
                TextPart(
                    text=f"Plan mode ON. Write your plan to: {plan_path}\n"
                    "Use ExitPlanMode when done, or /plan off to exit manually."
                )
            )
        else:
            wire_send(TextPart(text="Plan mode OFF. All tools are now available."))
        wire_send(StatusUpdate(plan_mode=soul.plan_mode))


@registry.command(name="add-dir")
async def add_dir(soul: NovelSoul, args: str):
    """将目录添加到工作空间。用法：/add-dir <path>。不带参数运行可列出已添加的目录。

    Args:
        soul: NovelSoul 实例。
        args: 要添加的目录路径。
    """
    from kaos.path import KaosPath

    from novel_cli.utils.path import is_within_directory, list_directory

    args = sanitize_cli_path(args)
    if not args:
        if not soul.runtime.additional_dirs:
            wire_send(TextPart(text="No additional directories. Usage: /add-dir <path>"))
        else:
            lines = ["Additional directories:"]
            for d in soul.runtime.additional_dirs:
                lines.append(f"  - {d}")
            wire_send(TextPart(text="\n".join(lines)))
        return

    path = KaosPath(args).expanduser().canonical()

    if not await path.exists():
        wire_send(TextPart(text=f"Directory does not exist: {path}"))
        return
    if not await path.is_dir():
        wire_send(TextPart(text=f"Not a directory: {path}"))
        return

    # 检查是否已添加（精确匹配）
    if path in soul.runtime.additional_dirs:
        wire_send(TextPart(text=f"Directory already in workspace: {path}"))
        return

    # 检查是否在工作目录内（已可访问）
    work_dir = soul.runtime.builtin_args.NOVEL_WORK_DIR
    if is_within_directory(path, work_dir):
        wire_send(TextPart(text=f"Directory is already within the working directory: {path}"))
        return

    # 检查是否在已添加的额外目录内（冗余）
    for existing in soul.runtime.additional_dirs:
        if is_within_directory(path, existing):
            wire_send(
                TextPart(
                    text=f"Directory is already within an added directory `{existing}`: {path}"
                )
            )
            return

    # 在提交任何状态变更前验证可读性
    try:
        ls_output = await list_directory(path)
    except OSError as e:
        wire_send(TextPart(text=f"Cannot read directory: {path} ({e})"))
        return

    # 添加目录（仅确认可读性后）
    soul.runtime.additional_dirs.append(path)

    # 持久化到会话状态
    soul.runtime.session.state.additional_dirs.append(str(path))
    soul.runtime.session.save_state()

    # 注入一条系统消息告知 LLM 关于新目录
    system_message = system(
        f"The user has added an additional directory to the workspace: `{path}`\n\n"
        f"Directory listing:\n```\n{ls_output}\n```\n\n"
        "You can now read, write, search, and glob files in this directory "
        "as if it were part of the working directory."
    )
    await soul.context.append_message(Message(role="user", content=[system_message]))

    wire_send(TextPart(text=f"Added directory to workspace: {path}"))
    logger.info("Added additional directory: {path}", path=path)


@registry.command
async def export(soul: NovelSoul, args: str):
    """将当前会话上下文导出为 markdown 文件。

    Args:
        soul: NovelSoul 实例。
        args: 可选的导出参数（目标路径等）。
    """
    from novel_cli.utils.export import perform_export

    session = soul.runtime.session
    result = await perform_export(
        history=list(soul.context.history),
        session_id=session.id,
        work_dir=str(session.work_dir),
        token_count=soul.context.token_count,
        args=args,
        default_dir=Path(str(session.work_dir)),
    )
    if isinstance(result, str):
        wire_send(TextPart(text=result))
        return
    output, count = result
    display = shorten_home(KaosPath(str(output)))
    wire_send(TextPart(text=f"Exported {count} messages to {display}"))
    wire_send(
        TextPart(
            text="  Note: The exported file may contain sensitive information. "
            "Please be cautious when sharing it externally."
        )
    )


@registry.command(name="import")
async def import_context(soul: NovelSoul, args: str):
    """从文件或会话 ID 导入上下文。

    Args:
        soul: NovelSoul 实例。
        args: 文件路径或会话 ID。
    """
    from novel_cli.utils.export import perform_import

    target = sanitize_cli_path(args)
    if not target:
        wire_send(TextPart(text="Usage: /import <file_path or session_id>"))
        return

    session = soul.runtime.session
    raw_max_context_size = (
        soul.runtime.llm.max_context_size if soul.runtime.llm is not None else None
    )
    max_context_size = (
        raw_max_context_size
        if isinstance(raw_max_context_size, int) and raw_max_context_size > 0
        else None
    )
    result = await perform_import(
        target=target,
        current_session_id=session.id,
        work_dir=session.work_dir,
        context=soul.context,
        max_context_size=max_context_size,
    )
    if isinstance(result, str):
        wire_send(TextPart(text=result))
        return

    source_desc, content_len = result
    wire_send(TextPart(text=f"Imported context from {source_desc} ({content_len} chars)."))
    if source_desc.startswith("file") and is_sensitive_file(Path(target).name):
        wire_send(
            TextPart(
                text="Warning: This file may contain secrets (API keys, tokens, credentials). "
                "The content is now part of your session context."
            )
        )


@registry.command
async def book(soul: NovelSoul, args: str):
    """选择当前查询的书籍。用法：/book [书名|none]。不带参数运行可浏览可用书籍列表。

    Args:
        soul: NovelSoul 实例。
        args: 可选的书名参数，或 "none" 清除选书。
    """
    from novel_cli.store import NovelStore
    from novel_cli.wire.types import BookList

    config = soul.runtime.config
    novel_db = config.services.novel_db

    # 数据库未配置时提示
    if not novel_db.pg_password.get_secret_value():
        wire_send(TextPart(text="小说知识库未配置，请配置数据库连接"))
        return

    args = args.strip()

    # /book none - 清除选书
    if args.lower() == "none":
        session = soul.runtime.session
        session.state.current_book = None
        session.save_state()
        wire_send(TextPart(text="已清除当前选书"))
        return

    # 有参数时精确匹配
    if args:
        store = NovelStore(novel_db)
        try:
            await store.connect()
            books = await store.list_books()
        finally:
            await store.close()

        # 精确匹配
        exact_match = [b for b in books if b == args]
        if exact_match:
            book_name = exact_match[0]
            session = soul.runtime.session
            session.state.current_book = book_name
            session.save_state()
            wire_send(TextPart(text=f"已选择书籍: {book_name}"))
            return

        # 未匹配到
        wire_send(TextPart(text=f"未找到书籍: {args}。请使用 /book 浏览可用书籍"))
        return

    # 无参数：发送 BookList 消息
    store = NovelStore(novel_db)
    try:
        await store.connect()
        books = await store.list_books()
    finally:
        await store.close()

    current_book = soul.runtime.session.state.current_book
    wire_send(BookList(books=books, current_book=current_book))
