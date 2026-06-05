"""工具模块初始化。

提供工具调用相关的辅助函数和异常类。
"""

import json
from typing import cast

import streamingjson  # type: ignore[reportMissingTypeStubs]
from kaos.path import KaosPath
from kosong.utils.typing import JsonType

from novel_cli.utils.string import shorten_middle


class SkipThisTool(Exception):
    """工具跳过异常。

    当工具决定跳过自身加载过程时抛出此异常。
    """

    pass


def extract_key_argument(json_content: str | streamingjson.Lexer, tool_name: str) -> str | None:
    """从工具调用的 JSON 参数中提取关键参数。

    根据不同的工具名称，从 JSON 参数中提取最具代表性的关键参数值，
    用于工具调用的标识和展示。

    Args:
        json_content: 工具参数的 JSON 内容，可以是字符串或 streamingjson 的 Lexer 对象。
        tool_name: 工具名称，用于确定提取逻辑。

    Returns:
        提取的关键参数字符串，如果无法提取则返回 None。
    """
    if isinstance(json_content, streamingjson.Lexer):
        json_str = json_content.complete_json()
    else:
        json_str = json_content
    try:
        curr_args: JsonType = json.loads(json_str, strict=False)
    except json.JSONDecodeError:
        return None
    if not curr_args:
        return None
    key_argument: str = ""
    match tool_name:
        case "Agent":
            if not isinstance(curr_args, dict) or not curr_args.get("description"):
                return None
            key_argument = str(curr_args["description"])
        case "SendDMail":
            return None
        case "Think":
            if not isinstance(curr_args, dict) or not curr_args.get("thought"):
                return None
            key_argument = str(curr_args["thought"])
        case "SetTodoList":
            return None
        case "Shell":
            if not isinstance(curr_args, dict) or not curr_args.get("command"):
                return None
            key_argument = str(curr_args["command"])
        case "TaskOutput":
            if not isinstance(curr_args, dict) or not curr_args.get("task_id"):
                return None
            key_argument = str(curr_args["task_id"])
        case "TaskList":
            if not isinstance(curr_args, dict):
                return None
            key_argument = "active" if curr_args.get("active_only", True) else "all"
        case "TaskStop":
            if not isinstance(curr_args, dict) or not curr_args.get("task_id"):
                return None
            key_argument = str(curr_args["task_id"])
        case "ReadFile":
            if not isinstance(curr_args, dict) or not curr_args.get("path"):
                return None
            key_argument = _normalize_path(str(curr_args["path"]))
        case "ReadMediaFile":
            if not isinstance(curr_args, dict) or not curr_args.get("path"):
                return None
            key_argument = _normalize_path(str(curr_args["path"]))
        case "Glob":
            if not isinstance(curr_args, dict) or not curr_args.get("pattern"):
                return None
            key_argument = str(curr_args["pattern"])
        case "Grep":
            if not isinstance(curr_args, dict) or not curr_args.get("pattern"):
                return None
            key_argument = str(curr_args["pattern"])
        case "WriteFile":
            if not isinstance(curr_args, dict) or not curr_args.get("path"):
                return None
            key_argument = _normalize_path(str(curr_args["path"]))
        case "StrReplaceFile":
            if not isinstance(curr_args, dict) or not curr_args.get("path"):
                return None
            key_argument = _normalize_path(str(curr_args["path"]))
        case "SearchWeb":
            if not isinstance(curr_args, dict) or not curr_args.get("query"):
                return None
            key_argument = str(curr_args["query"])
        case "FetchURL":
            if not isinstance(curr_args, dict) or not curr_args.get("url"):
                return None
            key_argument = str(curr_args["url"])
        case "SearchNovel" | "SearchEntity":
            if not isinstance(curr_args, dict):
                return None
            key_argument = str(curr_args.get("query") or curr_args.get("action", ""))
        case "SearchGraph":
            if not isinstance(curr_args, dict):
                return None
            key_argument = str(curr_args.get("entity_id") or curr_args.get("sub_action", ""))
        case "SearchCorpus":
            if not isinstance(curr_args, dict):
                return None
            key_argument = str(curr_args.get("keywords") or curr_args.get("chapter_id", ""))
        case _:
            if isinstance(json_content, streamingjson.Lexer):
                # 根据 streamingjson 源码，lexer.json_content 是 list[str] 类型
                content: list[str] = cast(list[str], json_content.json_content)  # type: ignore[reportUnknownMemberType]
                key_argument = "".join(content)
            else:
                key_argument = json_content
    key_argument = shorten_middle(key_argument, width=50)
    return key_argument


def _normalize_path(path: str) -> str:
    """规范化路径，移除当前工作目录前缀。

    Args:
        path: 原始路径字符串。

    Returns:
        规范化后的相对路径（如果原路径在当前工作目录下），
        否则返回原路径。
    """
    cwd = str(KaosPath.cwd().canonical())
    if path.startswith(cwd):
        path = path[len(cwd) :].lstrip("/\\")
    return path
