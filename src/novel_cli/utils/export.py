"""会话导出与导入模块。

本模块提供会话历史的导出和导入功能，支持将对话记录导出为 Markdown 格式文件，
以及导入外部文件或历史会话作为上下文。

主要功能：
- 导出会话历史为结构化的 Markdown 文档
- 导入文本文件或历史会话作为当前会话的上下文
- 导入内容的 Token 预算检查
- 敏感文件检测与警告
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import aiofiles
from kaos.path import KaosPath
from kosong.message import Message

from novel_cli.notifications.llm import is_notification_message
from novel_cli.soul.message import is_system_reminder_message, system
from novel_cli.utils.message import message_stringify
from novel_cli.utils.path import sanitize_cli_path
from novel_cli.utils.string import shorten
from novel_cli.wire.types import (
    AudioURLPart,
    ContentPart,
    ImageURLPart,
    TextPart,
    ThinkPart,
    ToolCall,
    VideoURLPart,
)

if TYPE_CHECKING:
    from novel_cli.soul.context import Context

# ---------------------------------------------------------------------------
# 导出辅助函数
# ---------------------------------------------------------------------------

_HINT_KEYS = ("path", "file_path", "command", "query", "url", "name", "pattern")
"""工具调用参数的常用键名，这些键对应的值适合用作简短的提示信息。"""


def _is_checkpoint_message(msg: Message) -> bool:
    """检查消息是否为内部检查点标记。

    Args:
        msg: 要检查的消息对象。

    Returns:
        如果消息是内部检查点标记则返回 True，否则返回 False。
    """
    if msg.role != "user" or len(msg.content) != 1:
        return False
    part = msg.content[0]
    return isinstance(part, TextPart) and part.text.strip().startswith("<system>CHECKPOINT")


def _is_internal_user_message(msg: Message) -> bool:
    """检查用户消息是否为内部记录而非真实用户输入。

    Args:
        msg: 要检查的消息对象。

    Returns:
        如果消息是内部记录则返回 True，否则返回 False。
    """
    return (
        _is_checkpoint_message(msg)
        or is_system_reminder_message(msg)
        or is_notification_message(msg)
    )


def _extract_tool_call_hint(args_json: str) -> str:
    """从工具调用参数中提取简短的人类可读提示。

    查找已知键（path、command 等），若未找到则使用第一个短字符串值作为回退。
    当未找到有用的值时返回空字符串。

    Args:
        args_json: 工具调用参数的 JSON 字符串。

    Returns:
        提取的提示文本，若未找到则返回空字符串。
    """
    try:
        parsed: object = json.loads(args_json, strict=False)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    args = cast(dict[str, object], parsed)

    # 优先使用已知键
    for key in _HINT_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return shorten(val, width=60)

    # 回退：使用第一个短字符串值
    for val in args.values():
        if isinstance(val, str) and 0 < len(val) <= 80:
            return shorten(val, width=60)

    return ""


def _format_content_part_md(part: ContentPart) -> str:
    """将单个 ContentPart 转换为 Markdown 文本。

    Args:
        part: 内容部分对象。

    Returns:
        转换后的 Markdown 文本。
    """
    match part:
        case TextPart(text=text):
            return text
        case ThinkPart(think=think):
            if not think.strip():
                return ""
            return f"<details><summary>Thinking</summary>\n\n{think}\n\n</details>"
        case ImageURLPart():
            return "[image]"
        case AudioURLPart():
            return "[audio]"
        case VideoURLPart():
            return "[video]"
        case _:
            return f"[{part.type}]"


def _format_tool_call_md(tool_call: ToolCall) -> str:
    """将 ToolCall 转换为带有可读标题的 Markdown 子节。

    Args:
        tool_call: 工具调用对象。

    Returns:
        转换后的 Markdown 子节文本。
    """
    args_raw = tool_call.function.arguments or "{}"
    hint = _extract_tool_call_hint(args_raw)
    title = f"#### Tool Call: {tool_call.function.name}"
    if hint:
        title += f" (`{hint}`)"

    try:
        parsed = json.loads(args_raw, strict=False)
        args_formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        args_formatted = args_raw

    return f"{title}\n<!-- call_id: {tool_call.id} -->\n```json\n{args_formatted}\n```"


def _format_tool_result_md(msg: Message, tool_name: str, hint: str) -> str:
    """将工具结果消息格式化为可折叠的 Markdown 块。

    Args:
        msg: 工具结果消息对象。
        tool_name: 工具名称。
        hint: 工具调用的提示文本。

    Returns:
        格式化后的 Markdown 块文本。
    """
    call_id = msg.tool_call_id or "unknown"

    # 使用 _format_content_part_md 以保持与模块其他部分的一致性
    # （message_stringify 会丢失 ThinkPart 并泄露 <system> 标记）
    result_parts: list[str] = []
    for part in msg.content:
        text = _format_content_part_md(part)
        if text.strip():
            result_parts.append(text)
    result_text = "\n".join(result_parts)

    summary = f"Tool Result: {tool_name}"
    if hint:
        summary += f" (`{hint}`)"

    return (
        f"<details><summary>{summary}</summary>\n\n"
        f"<!-- call_id: {call_id} -->\n"
        f"{result_text}\n\n"
        "</details>"
    )


def _group_into_turns(history: Sequence[Message]) -> list[list[Message]]:
    """将消息分组为逻辑轮次，每个轮次以真实用户消息开始。

    Args:
        history: 消息历史序列。

    Returns:
        分组后的消息轮次列表。
    """
    turns: list[list[Message]] = []
    current: list[Message] = []

    for msg in history:
        if _is_internal_user_message(msg):
            continue
        if msg.role == "user" and current:
            turns.append(current)
            current = []
        current.append(msg)

    if current:
        turns.append(current)
    return turns


def _format_turn_md(messages: list[Message], turn_number: int) -> str:
    """将逻辑轮次格式化为 Markdown 节。

    一个轮次通常包含：
      用户消息 -> 助手（思考 + 文本 + 工具调用） -> 工具结果
      -> 助手（更多文本 + 工具调用） -> 工具结果 -> 助手（最终）
    所有助手/工具消息都归组在同一个 ``### Assistant`` 标题下。

    Args:
        messages: 该轮次的消息列表。
        turn_number: 轮次编号。

    Returns:
        格式化后的 Markdown 节文本。
    """
    lines: list[str] = [f"## Turn {turn_number}", ""]

    # tool_call_id -> (function_name, hint)
    tool_call_info: dict[str, tuple[str, str]] = {}
    assistant_header_written = False

    for msg in messages:
        if _is_internal_user_message(msg):
            continue

        if msg.role == "user":
            lines.append("### User")
            lines.append("")
            for part in msg.content:
                text = _format_content_part_md(part)
                if text.strip():
                    lines.append(text)
                    lines.append("")

        elif msg.role == "assistant":
            if not assistant_header_written:
                lines.append("### Assistant")
                lines.append("")
                assistant_header_written = True

            # 内容部分（思考、文本、媒体）
            for part in msg.content:
                text = _format_content_part_md(part)
                if text.strip():
                    lines.append(text)
                    lines.append("")

            # 工具调用
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    hint = _extract_tool_call_hint(tc.function.arguments or "{}")
                    tool_call_info[tc.id] = (tc.function.name, hint)
                    lines.append(_format_tool_call_md(tc))
                    lines.append("")

        elif msg.role == "tool":
            tc_id = msg.tool_call_id or ""
            name, hint = tool_call_info.get(tc_id, ("unknown", ""))
            lines.append(_format_tool_result_md(msg, name, hint))
            lines.append("")

        elif msg.role in ("system", "developer"):
            lines.append(f"### {msg.role.capitalize()}")
            lines.append("")
            for part in msg.content:
                text = _format_content_part_md(part)
                if text.strip():
                    lines.append(text)
                    lines.append("")

    return "\n".join(lines)


def _build_overview(
    history: Sequence[Message],
    turns: list[list[Message]],
    token_count: int,
) -> str:
    """从现有数据构建概览节（不调用 LLM）。

    Args:
        history: 消息历史序列。
        turns: 分组后的消息轮次列表。
        token_count: Token 计数。

    Returns:
        构建好的概览节文本。
    """
    # 主题：第一条真实用户消息文本，截断处理
    topic = ""
    for msg in history:
        if msg.role == "user" and not _is_internal_user_message(msg):
            topic = shorten(message_stringify(msg), width=80)
            break

    # 统计所有消息中的工具调用数量
    n_tool_calls = sum(len(msg.tool_calls) for msg in history if msg.tool_calls)

    lines = [
        "## Overview",
        "",
        f"- **Topic**: {topic}" if topic else "- **Topic**: (empty)",
        f"- **Conversation**: {len(turns)} turns | "
        f"{n_tool_calls} tool calls | {token_count:,} tokens",
        "",
        "---",
    ]
    return "\n".join(lines)


def build_export_markdown(
    session_id: str,
    work_dir: str,
    history: Sequence[Message],
    token_count: int,
    now: datetime,
) -> str:
    """构建完整的导出 Markdown 字符串。

    Args:
        session_id: 会话 ID。
        work_dir: 工作目录路径。
        history: 消息历史序列。
        token_count: Token 计数。
        now: 当前时间。

    Returns:
        构建好的完整 Markdown 文本。
    """
    lines: list[str] = [
        "---",
        f"session_id: {session_id}",
        f"exported_at: {now.isoformat(timespec='seconds')}",
        f"work_dir: {work_dir}",
        f"message_count: {len(history)}",
        f"token_count: {token_count}",
        "---",
        "",
        "# Novel Session Export",
        "",
    ]

    turns = _group_into_turns(history)
    lines.append(_build_overview(history, turns, token_count))
    lines.append("")

    for idx, turn_messages in enumerate(turns):
        lines.append(_format_turn_md(turn_messages, idx + 1))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 导入辅助函数
# ---------------------------------------------------------------------------

_IMPORTABLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Markdown / 纯文本
        ".md",
        ".markdown",
        ".txt",
        ".text",
        ".rst",
        # 数据 / 配置
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".csv",
        ".tsv",
        ".xml",
        ".env",
        ".properties",
        # 源代码
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".bat",
        ".cmd",
        ".r",
        ".R",
        ".lua",
        ".pl",
        ".pm",
        ".ex",
        ".exs",
        ".erl",
        ".hs",
        ".ml",
        ".sql",
        ".graphql",
        ".proto",
        # Web
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".svg",
        # 日志
        ".log",
        # 文档
        ".tex",
        ".bib",
        ".org",
        ".adoc",
        ".wiki",
    }
)
"""``/import`` 命令接受的文件扩展名列表。仅支持文本格式文件，
导入二进制文件（图片、PDF、压缩包等）会被拒绝并给出友好提示。"""


def is_importable_file(path_str: str) -> bool:
    """检查路径字符串的扩展名是否在可导入白名单中。

    无扩展名的文件也被接受（可能是 README、Makefile 等）。

    Args:
        path_str: 文件路径字符串。

    Returns:
        如果文件扩展名可导入则返回 True，否则返回 False。
    """
    suffix = Path(path_str).suffix.lower()
    return suffix == "" or suffix in _IMPORTABLE_EXTENSIONS


def _stringify_content_parts(parts: Sequence[ContentPart]) -> str:
    """将 ContentPart 列表序列化为可读文本，保留 ThinkPart。

    Args:
        parts: 内容部分列表。

    Returns:
        序列化后的文本。
    """
    segments: list[str] = []
    for part in parts:
        match part:
            case TextPart(text=text):
                if text.strip():
                    segments.append(text)
            case ThinkPart(think=think):
                if think.strip():
                    segments.append(f"<thinking>\n{think}\n</thinking>")
            case ImageURLPart():
                segments.append("[image]")
            case AudioURLPart():
                segments.append("[audio]")
            case VideoURLPart():
                segments.append("[video]")
            case _:
                segments.append(f"[{part.type}]")
    return "\n".join(segments)


def _stringify_tool_calls(tool_calls: Sequence[ToolCall]) -> str:
    """将工具调用序列化为可读文本。

    Args:
        tool_calls: 工具调用序列。

    Returns:
        序列化后的文本。
    """
    lines: list[str] = []
    for tc in tool_calls:
        args_raw = tc.function.arguments or "{}"
        try:
            args = json.loads(args_raw, strict=False)
            args_str = json.dumps(args, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            args_str = args_raw
        lines.append(f"Tool Call: {tc.function.name}({args_str})")
    return "\n".join(lines)


def stringify_context_history(history: Sequence[Message]) -> str:
    """将消息序列转换为可读文本记录。

    保留 ThinkPart 内容、工具调用信息和工具结果，
    以便接收导入上下文的 AI 能够获得完整信息。

    Args:
        history: 消息历史序列。

    Returns:
        转换后的文本记录。
    """
    parts: list[str] = []
    for msg in history:
        if _is_internal_user_message(msg):
            continue

        role_label = msg.role.upper()
        segments: list[str] = []

        # 内容部分（文本、思考、媒体）
        content_text = _stringify_content_parts(msg.content)
        if content_text.strip():
            segments.append(content_text)

        # 工具调用（仅助手消息）
        if msg.tool_calls:
            segments.append(_stringify_tool_calls(msg.tool_calls))

        if not segments:
            continue

        header = f"[{role_label}]"
        if msg.role == "tool" and msg.tool_call_id:
            header = f"[{role_label}] (call_id: {msg.tool_call_id})"

        parts.append(f"{header}\n" + "\n".join(segments))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 共享命令逻辑
# ---------------------------------------------------------------------------


async def perform_export(
    history: Sequence[Message],
    session_id: str,
    work_dir: str,
    token_count: int,
    args: str,
    default_dir: Path,
) -> tuple[Path, int] | str:
    """执行完整的导出操作。

    Args:
        history: 消息历史序列。
        session_id: 会话 ID。
        work_dir: 工作目录路径。
        token_count: Token 计数。
        args: 用户提供的导出路径参数。
        default_dir: 默认导出目录。

    Returns:
        成功时返回 ``(output_path, message_count)``，失败时返回错误消息字符串。
    """
    if not history:
        return "No messages to export."

    now = datetime.now().astimezone()
    short_id = session_id[:8]
    default_name = f"novel-export-{short_id}-{now.strftime('%Y%m%d-%H%M%S')}.md"

    cleaned = sanitize_cli_path(args)
    if cleaned:
        # sanitize_cli_path 仅移除引号，保留尾随分隔符
        directory_hint = cleaned.endswith(("/", "\\"))
        output = Path(cleaned).expanduser()
        if not output.is_absolute():
            output = default_dir / output
        # 保持显式的"目录意图"，即使该目录尚未存在
        if directory_hint or output.is_dir():
            output = output / default_name
    else:
        output = default_dir / default_name

    content = build_export_markdown(
        session_id=session_id,
        work_dir=work_dir,
        history=history,
        token_count=token_count,
        now=now,
    )

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(output, "w", encoding="utf-8") as f:
            await f.write(content)
    except OSError as e:
        return f"Failed to write export file: {e}"

    return (output, len(history))


MAX_IMPORT_SIZE = 10 * 1024 * 1024  # 10 MB
"""``/import`` 命令允许导入的最大文件大小（字节）。"""

_SENSITIVE_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    "credentials",
    "secrets",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
)
"""可能包含敏感内容的文件名子串列表。"""


def is_sensitive_file(filename: str) -> bool:
    """检查文件名是否可能包含敏感信息。

    Args:
        filename: 文件名。

    Returns:
        如果文件名看起来可能包含敏感信息则返回 True，否则返回 False。
    """
    name = filename.lower()
    return any(pat in name for pat in _SENSITIVE_FILE_PATTERNS)


def _validate_import_token_budget(
    estimated_tokens: int,
    current_token_count: int,
    max_context_size: int | None,
) -> str | None:
    """验证导入是否会超出会话的 Token 预算。

    *estimated_tokens* 是导入消息的预估 Token 数量。
    检查逻辑为 ``current_token_count + estimated_tokens <= max_context_size``。

    Args:
        estimated_tokens: 导入内容的预估 Token 数量。
        current_token_count: 当前 Token 计数。
        max_context_size: 最大上下文大小。

    Returns:
        如果导入会超出预算则返回错误消息，否则返回 None。
    """
    if max_context_size is None or max_context_size <= 0:
        return None

    total_after_import = current_token_count + estimated_tokens
    if total_after_import <= max_context_size:
        return None

    return (
        "Imported content is too large for the current model context "
        f"(~{estimated_tokens:,} import tokens + {current_token_count:,} existing "
        f"= ~{total_after_import:,} total > {max_context_size:,} token limit). "
        "Please import a smaller file or session."
    )


async def resolve_import_source(
    target: str,
    current_session_id: str,
    work_dir: KaosPath,
) -> tuple[str, str] | str:
    """解析导入源，返回 ``(content, source_desc)`` 或错误消息。

    此函数处理 I/O 和源级别验证（文件类型、编码、字节大小上限）。
    会话级别的 Token 预算检查由 :func:`perform_import` 处理。

    Args:
        target: 导入目标（文件路径或会话 ID）。
        current_session_id: 当前会话 ID。
        work_dir: 工作目录。

    Returns:
        成功时返回 ``(content, source_desc)``，失败时返回错误消息字符串。
    """
    from novel_cli.session import Session
    from novel_cli.soul.context import Context

    target_path = Path(target).expanduser()
    if not target_path.is_absolute():
        target_path = Path(str(work_dir)) / target_path

    if target_path.exists() and target_path.is_dir():
        return "The specified path is a directory; please provide a file to import."

    if target_path.exists() and target_path.is_file():
        if not is_importable_file(target_path.name):
            return (
                f"Unsupported file type '{target_path.suffix}'. "
                "/import only supports text-based files "
                "(e.g. .md, .txt, .json, .py, .log, …)."
            )

        try:
            file_size = target_path.stat().st_size
        except OSError as e:
            return f"Failed to read file: {e}"
        if file_size > MAX_IMPORT_SIZE:
            limit_mb = MAX_IMPORT_SIZE // (1024 * 1024)
            return (
                f"File is too large ({file_size / 1024 / 1024:.1f} MB). "
                f"Maximum import size is {limit_mb} MB."
            )

        try:
            async with aiofiles.open(target_path, encoding="utf-8") as f:
                content = await f.read()
        except UnicodeDecodeError:
            return (
                f"Cannot import '{target_path.name}': "
                "the file does not appear to be valid UTF-8 text."
            )
        except OSError as e:
            return f"Failed to read file: {e}"

        if not content.strip():
            return "The file is empty, nothing to import."

        return (content, f"file '{target_path.name}'")

    # 不是磁盘上的文件 — 尝试作为会话 ID
    if target == current_session_id:
        return "Cannot import the current session into itself."

    source_session = await Session.find(work_dir, target)
    if source_session is None:
        return f"'{target}' is not a valid file path or session ID."

    source_context = Context(source_session.context_file)
    try:
        restored = await source_context.restore()
    except Exception as e:
        return f"Failed to load source session: {e}"
    if not restored or not source_context.history:
        return "The source session has no messages."

    content = stringify_context_history(source_context.history)
    content_bytes = len(content.encode("utf-8"))
    if content_bytes > MAX_IMPORT_SIZE:
        limit_mb = MAX_IMPORT_SIZE // (1024 * 1024)
        actual_mb = content_bytes / 1024 / 1024
        return (
            f"Session content is too large ({actual_mb:.1f} MB). "
            f"Maximum import size is {limit_mb} MB."
        )
    return (content, f"session '{target}'")


def build_import_message(content: str, source_desc: str) -> Message:
    """构建用于导入操作的 ``Message``，准备添加到上下文。

    Args:
        content: 导入的内容文本。
        source_desc: 导入源的描述。

    Returns:
        构建好的 Message 对象。
    """
    import_text = f'<imported_context source="{source_desc}">\n{content}\n</imported_context>'
    return Message(
        role="user",
        content=[
            system(
                f"The user has imported context from {source_desc}. "
                "This is a prior conversation history that may be relevant "
                "to the current session. "
                "Please review this context and use it to inform your responses."
            ),
            TextPart(text=import_text),
        ],
    )


async def perform_import(
    target: str,
    current_session_id: str,
    work_dir: KaosPath,
    context: Context,
    max_context_size: int | None = None,
) -> tuple[str, int] | str:
    """高层导入操作：解析源、验证、构建消息、更新上下文。

    Args:
        target: 导入目标（文件路径或会话 ID）。
        current_session_id: 当前会话 ID。
        work_dir: 工作目录。
        context: 会话上下文对象。
        max_context_size: 最大上下文大小，可选。

    Returns:
        成功时返回 ``(source_desc, content_len)``，
        失败时返回错误消息字符串。
        *content_len* 是原始导入内容的字符长度（不含包装标记），
        适合用于用户界面显示。
        调用者负责处理其他副作用（wire 文件写入、UI 输出等）。
    """
    from novel_cli.soul.compaction import estimate_text_tokens

    result = await resolve_import_source(
        target=target,
        current_session_id=current_session_id,
        work_dir=work_dir,
    )
    if isinstance(result, str):
        return result

    content, source_desc = result
    message = build_import_message(content, source_desc)

    # Token 预算检查 — 在修改上下文前拒绝
    estimated = estimate_text_tokens([message])
    if error := _validate_import_token_budget(estimated, context.token_count, max_context_size):
        return error

    await context.append_message(message)
    await context.update_token_count(context.token_count + estimated)

    return (source_desc, len(content))
