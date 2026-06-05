from __future__ import annotations

# ruff: noqa

from inline_snapshot import snapshot

from novel_cli.tools.agent import Agent as AgentTool
from novel_cli.tools.background import TaskList, TaskOutput, TaskStop
from novel_cli.tools.dmail import SendDMail
from novel_cli.tools.file.glob import Glob
from novel_cli.tools.file.grep_local import Grep
from novel_cli.tools.file.read import ReadFile
from novel_cli.tools.file.read_media import ReadMediaFile
from novel_cli.tools.file.replace import StrReplaceFile
from novel_cli.tools.file.write import WriteFile
from novel_cli.tools.shell import Shell
from novel_cli.tools.think import Think
from novel_cli.tools.todo import SetTodoList
from novel_cli.tools.web.fetch import FetchURL
from novel_cli.tools.web.search import SearchWeb


def test_agent_params_schema(agent_tool: AgentTool):
    """Test the schema of Agent tool parameters."""
    assert agent_tool.base.parameters == snapshot(
        {
            "description": """\
Agent 工具的参数模型。

Attributes:
    description: 任务的简短描述（3-5 个词）。
    prompt: 智能体要执行的任务描述。
    subagent_type: 内置智能体类型，默认为 `coder`。
    model: 可选的模型覆盖。选择优先级：此参数 > 内置类型默认模型 > 父智能体当前模型。
    resume: 可选，要恢复的智能体实例 ID，而不是创建新实例。
    run_in_background: 是否在后台运行智能体。除非任务可以独立继续且提前返回控制权
        有明显好处，否则应优先使用前台模式。
    timeout: 智能体任务的超时时间（秒）。前台模式无默认超时（运行至完成），
        最大 3600 秒（1 小时）。后台模式默认使用配置值（15 分钟），
        最大 3600 秒（1 小时）。超时后智能体将被停止。\
""",
            "properties": {
                "description": {
                    "description": "A short (3-5 word) description of the task",
                    "type": "string",
                },
                "prompt": {
                    "description": "The task for the agent to perform",
                    "type": "string",
                },
                "subagent_type": {
                    "default": "coder",
                    "description": "The built-in agent type to use. Defaults to `coder`.",
                    "type": "string",
                },
                "model": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "description": "Optional model override. Selection priority is: this parameter, then the built-in type default model, then the parent agent's current model.",
                },
                "resume": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "description": "Optional agent ID to resume instead of creating a new instance.",
                },
                "run_in_background": {
                    "default": False,
                    "description": "Whether to run the agent in the background. Prefer false unless the task can continue independently and there is a clear benefit to returning control before the result is needed.",
                    "type": "boolean",
                },
                "timeout": {
                    "anyOf": [
                        {"maximum": 3600, "minimum": 30, "type": "integer"},
                        {"type": "null"},
                    ],
                    "default": None,
                    "description": "Timeout in seconds for the agent task. Foreground: no default timeout (runs until completion), max 3600s (1hr). Background: default from config (15min), max 3600s (1hr). The agent is stopped if it exceeds this limit.",
                },
            },
            "required": ["description", "prompt"],
            "type": "object",
        }
    )


def test_send_dmail_params_schema(send_dmail_tool: SendDMail):
    """Test the schema of SendDMail tool parameters."""
    assert send_dmail_tool.base.parameters == snapshot(
        {
            "description": """\
D-Mail 消息模型。

Attributes:
    message: 要发送的消息内容。
    checkpoint_id: 目标检查点 ID，消息将发送回该检查点。\
""",
            "properties": {
                "message": {"description": "The message to send.", "type": "string"},
                "checkpoint_id": {
                    "description": "The checkpoint to send the message back to.",
                    "minimum": 0,
                    "type": "integer",
                },
            },
            "required": ["message", "checkpoint_id"],
            "type": "object",
        }
    )


def test_think_params_schema(think_tool: Think):
    """Test the schema of Think tool parameters."""
    assert think_tool.base.parameters == snapshot(
        {
            "description": """\
思考工具参数。

Attributes:
    thought: 要记录的思考内容。\
""",
            "properties": {
                "thought": {
                    "description": "A thought to think about.",
                    "type": "string",
                }
            },
            "required": ["thought"],
            "type": "object",
        }
    )


def test_set_todo_list_params_schema(set_todo_list_tool: SetTodoList):
    """Test the schema of SetTodoList tool parameters."""
    assert set_todo_list_tool.base.parameters == snapshot(
        {
            "description": """\
待办列表参数。

Attributes:
    todos: 更新的待办列表。如果不提供，则返回当前待办列表而不做任何更改。\
""",
            "properties": {
                "todos": {
                    "anyOf": [
                        {
                            "items": {
                                "description": """\
待办事项模型。

Attributes:
    title: 待办事项标题。
    status: 待办事项状态，可选值为 pending、in_progress、done。\
""",
                                "properties": {
                                    "title": {
                                        "description": "The title of the todo",
                                        "minLength": 1,
                                        "type": "string",
                                    },
                                    "status": {
                                        "description": "The status of the todo",
                                        "enum": ["pending", "in_progress", "done"],
                                        "type": "string",
                                    },
                                },
                                "required": ["title", "status"],
                                "type": "object",
                            },
                            "type": "array",
                        },
                        {"type": "null"},
                    ],
                    "default": None,
                    "description": "The updated todo list. If not provided, returns the current todo list without making changes.",
                }
            },
            "type": "object",
        }
    )


def test_shell_params_schema(shell_tool: Shell):
    """Test the schema of Shell tool parameters."""
    assert shell_tool.base.parameters == snapshot(
        {
            "description": """\
Shell 工具的参数模型。

Attributes:
    command: 要执行的命令。
    timeout: 命令执行的超时时间（秒）。超时后命令将被终止。
    run_in_background: 是否将命令作为后台任务运行。
    description: 后台任务的简短描述。当 run_in_background=true 时必填。\
""",
            "properties": {
                "command": {
                    "description": "The command to execute.",
                    "type": "string",
                },
                "timeout": {
                    "default": 60,
                    "description": "The timeout in seconds for the command to execute. If the command takes longer than this, it will be killed.",
                    "maximum": 86400,
                    "minimum": 1,
                    "type": "integer",
                },
                "run_in_background": {
                    "default": False,
                    "description": "Whether to run the command as a background task.",
                    "type": "boolean",
                },
                "description": {
                    "default": "",
                    "description": "A short description for the background task. Required when run_in_background=true.",
                    "type": "string",
                },
            },
            "required": ["command"],
            "type": "object",
        }
    )


def test_task_output_params_schema(task_output_tool: TaskOutput):
    assert task_output_tool.base.parameters == snapshot(
        {
            "description": """\
TaskOutput 工具的参数模型。

Attributes:
    task_id: 要查看的后台任务 ID。
    block: 是否等待任务完成后返回。
    timeout: block=true 时的最大等待秒数。\
""",
            "properties": {
                "task_id": {
                    "description": "The background task ID to inspect.",
                    "type": "string",
                },
                "block": {
                    "default": False,
                    "description": "Whether to wait for the task to finish before returning.",
                    "type": "boolean",
                },
                "timeout": {
                    "default": 30,
                    "description": "Maximum number of seconds to wait when block=true.",
                    "maximum": 3600,
                    "minimum": 0,
                    "type": "integer",
                },
            },
            "required": ["task_id"],
            "type": "object",
        }
    )


def test_task_list_params_schema(task_list_tool: TaskList):
    assert task_list_tool.base.parameters == snapshot(
        {
            "description": """\
TaskList 工具的参数模型。

Attributes:
    active_only: 是否只列出非终止状态的后台任务。
    limit: 返回任务的最大数量。\
""",
            "properties": {
                "active_only": {
                    "default": True,
                    "description": "Whether to list only non-terminal background tasks.",
                    "type": "boolean",
                },
                "limit": {
                    "default": 20,
                    "description": "Maximum number of tasks to return.",
                    "maximum": 100,
                    "minimum": 1,
                    "type": "integer",
                },
            },
            "type": "object",
        }
    )


def test_task_stop_params_schema(task_stop_tool: TaskStop):
    assert task_stop_tool.base.parameters == snapshot(
        {
            "description": """\
TaskStop 工具的参数模型。

Attributes:
    task_id: 要停止的后台任务 ID。
    reason: 任务停止时记录的简短原因。\
""",
            "properties": {
                "task_id": {
                    "description": "The background task ID to stop.",
                    "type": "string",
                },
                "reason": {
                    "default": "Stopped by TaskStop",
                    "description": "Short reason recorded when the task is stopped.",
                    "type": "string",
                },
            },
            "required": ["task_id"],
            "type": "object",
        }
    )


def test_read_file_params_schema(read_file_tool: ReadFile):
    """Test the schema of ReadFile tool parameters."""
    assert read_file_tool.base.parameters == snapshot(
        {
            "description": """\
ReadFile 工具的参数模型。

Attributes:
    path: 要读取的文件路径。读取工作目录外的文件时需要使用绝对路径。
    line_offset: 开始读取的行号。默认从文件开头读取。负值表示从文件末尾开始读取
        （例如 -100 读取最后 100 行）。负值的绝对值不能超过 MAX_LINES。
    n_lines: 要读取的行数。默认读取最多 MAX_LINES 行，这也是允许的最大值。\
""",
            "properties": {
                "path": {
                    "description": "The path to the file to read. Absolute paths are required when reading files outside the working directory.",
                    "type": "string",
                },
                "line_offset": {
                    "default": 1,
                    "description": "The line number to start reading from. By default read from the beginning of the file. Set this when the file is too large to read at once. Negative values read from the end of the file (e.g. -100 reads the last 100 lines). The absolute value of negative offset cannot exceed 1000.",
                    "type": "integer",
                },
                "n_lines": {
                    "default": 1000,
                    "description": "The number of lines to read. By default read up to 1000 lines, which is the max allowed value. Set this value when the file is too large to read at once.",
                    "minimum": 1,
                    "type": "integer",
                },
            },
            "required": ["path"],
            "type": "object",
        }
    )


def test_read_media_file_params_schema(read_media_file_tool: ReadMediaFile):
    """Test the schema of ReadMediaFile tool parameters."""
    assert read_media_file_tool.base.parameters == snapshot(
        {
            "description": """\
ReadMediaFile 工具的参数模型。

Attributes:
    path: 要读取的文件路径。读取工作目录外的文件时需要使用绝对路径。\
""",
            "properties": {
                "path": {
                    "description": "The path to the file to read. Absolute paths are required when reading files outside the working directory.",
                    "type": "string",
                }
            },
            "required": ["path"],
            "type": "object",
        }
    )


def test_glob_params_schema(glob_tool: Glob):
    """Test the schema of Glob tool parameters."""
    assert glob_tool.base.parameters == snapshot(
        {
            "description": """\
Glob 搜索参数。

Attributes:
    pattern: glob 模式，用于匹配文件或目录。
    directory: 搜索目录的绝对路径，默认为工作目录。
    include_dirs: 是否在结果中包含目录。\
""",
            "properties": {
                "pattern": {
                    "description": "Glob pattern to match files/directories.",
                    "type": "string",
                },
                "directory": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "description": "Absolute path to the directory to search in (defaults to working directory).",
                },
                "include_dirs": {
                    "default": True,
                    "description": "Whether to include directories in results.",
                    "type": "boolean",
                },
            },
            "required": ["pattern"],
            "type": "object",
        }
    )


def test_grep_params_schema(grep_tool: Grep):
    """Test the schema of Grep tool parameters."""
    assert grep_tool.base.parameters == snapshot(
        {
            "description": """\
Grep 工具的参数模型。

Attributes:
    pattern: 要在文件内容中搜索的正则表达式模式。
    path: 要搜索的文件或目录，默认为当前工作目录。若指定必须为绝对路径。
    glob: 用于过滤文件的 glob 模式（如 `*.js`, `*.{ts,tsx}`）。默认无过滤。
    output_mode: 输出模式，支持 `content`、`files_with_matches`、`count_matches`。
    before_context: 匹配行前显示的行数（`-B` 选项）。需要 `output_mode` 为 `content`。
    after_context: 匹配行后显示的行数（`-A` 选项）。需要 `output_mode` 为 `content`。
    context: 匹配行前后显示的行数（`-C` 选项）。需要 `output_mode` 为 `content`。
    line_number: 是否在输出中显示行号（`-n` 选项）。需要 `output_mode` 为 `content`。
    ignore_case: 是否忽略大小写进行搜索（`-i` 选项）。
    type: 要搜索的文件类型，如 py、rust、js、ts、go、java 等。
    head_limit: 输出结果的行数/条数上限，相当于 `| head -N`。
    offset: 在应用 head_limit 之前跳过的行数/条数。
    multiline: 是否启用多行模式，使 `.` 匹配换行符。
    include_ignored: 是否包含被 .gitignore 等忽略规则排除的文件。\
""",
            "properties": {
                "pattern": {
                    "description": "The regular expression pattern to search for in file contents",
                    "type": "string",
                },
                "path": {
                    "default": ".",
                    "description": "File or directory to search in. Defaults to current working directory. If specified, it must be an absolute path.",
                    "type": "string",
                },
                "glob": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "description": "Glob pattern to filter files (e.g. `*.js`, `*.{ts,tsx}`). No filter by default.",
                },
                "output_mode": {
                    "default": "files_with_matches",
                    "description": "`content`: Show matching lines (supports `-B`, `-A`, `-C`, `-n`, `head_limit`); `files_with_matches`: Show file paths (supports `head_limit`); `count_matches`: Show total number of matches. Defaults to `files_with_matches`.",
                    "type": "string",
                },
                "-B": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                    "description": "Number of lines to show before each match (the `-B` option). Requires `output_mode` to be `content`.",
                },
                "-A": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                    "description": "Number of lines to show after each match (the `-A` option). Requires `output_mode` to be `content`.",
                },
                "-C": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                    "description": "Number of lines to show before and after each match (the `-C` option). Requires `output_mode` to be `content`.",
                },
                "-n": {
                    "default": True,
                    "description": "Show line numbers in output (the `-n` option). Requires `output_mode` to be `content`. Defaults to true.",
                    "type": "boolean",
                },
                "-i": {
                    "default": False,
                    "description": "Case insensitive search (the `-i` option).",
                    "type": "boolean",
                },
                "type": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "description": "File type to search. Examples: py, rust, js, ts, go, java, etc. More efficient than `glob` for standard file types.",
                },
                "head_limit": {
                    "anyOf": [{"minimum": 0, "type": "integer"}, {"type": "null"}],
                    "default": 250,
                    "description": "Limit output to first N lines/entries, equivalent to `| head -N`. Works across all output modes: content (limits output lines), files_with_matches (limits file paths), count_matches (limits count entries). Defaults to 250. Pass 0 for unlimited (use sparingly — large result sets waste context).",
                },
                "offset": {
                    "default": 0,
                    "description": "Skip first N lines/entries before applying head_limit, equivalent to `| tail -n +N | head -N`. Works across all output modes. Defaults to 0.",
                    "minimum": 0,
                    "type": "integer",
                },
                "multiline": {
                    "default": False,
                    "description": "Enable multiline mode where `.` matches newlines and patterns can span lines (the `-U` and `--multiline-dotall` options). By default, multiline mode is disabled.",
                    "type": "boolean",
                },
                "include_ignored": {
                    "default": False,
                    "description": "Include files that are ignored by `.gitignore`, `.ignore`, and other ignore rules. Useful for searching gitignored artifacts such as build outputs (e.g. `dist/`, `build/`) or `node_modules`. Sensitive files (like `.env`) remain filtered by the sensitive-file protection layer. Defaults to false.",
                    "type": "boolean",
                },
            },
            "required": ["pattern"],
            "type": "object",
        }
    )


def test_write_file_params_schema(write_file_tool: WriteFile):
    """Test the schema of WriteFile tool parameters."""
    assert write_file_tool.base.parameters == snapshot(
        {
            "description": """\
文件写入参数。

Attributes:
    path: 文件路径。在工作目录外写入文件时需要提供绝对路径。
    content: 要写入文件的内容。
    mode: 写入模式，支持 `overwrite`（覆盖整个文件）和 `append`（追加到文件末尾）。\
""",
            "properties": {
                "path": {
                    "description": "The path to the file to write. Absolute paths are required when writing files outside the working directory.",
                    "type": "string",
                },
                "content": {
                    "description": "The content to write to the file",
                    "type": "string",
                },
                "mode": {
                    "default": "overwrite",
                    "description": "The mode to use to write to the file. Two modes are supported: `overwrite` for overwriting the whole file and `append` for appending to the end of an existing file.",
                    "enum": ["overwrite", "append"],
                    "type": "string",
                },
            },
            "required": ["path", "content"],
            "type": "object",
        }
    )


def test_str_replace_file_params_schema(str_replace_file_tool: StrReplaceFile):
    """Test the schema of StrReplaceFile tool parameters."""
    assert str_replace_file_tool.base.parameters == snapshot(
        {
            "description": """\
文件替换参数。

Attributes:
    path: 文件路径。在工作目录外编辑文件时需要提供绝对路径。
    edit: 编辑操作，可以是单个编辑或编辑列表。\
""",
            "properties": {
                "path": {
                    "description": "The path to the file to edit. Absolute paths are required when editing files outside the working directory.",
                    "type": "string",
                },
                "edit": {
                    "anyOf": [
                        {
                            "description": """\
编辑操作参数。

Attributes:
    old: 要替换的旧字符串，可以是多行文本。
    new: 替换后的新字符串，可以是多行文本。
    replace_all: 是否替换所有匹配项，默认只替换第一个匹配项。\
""",
                            "properties": {
                                "old": {
                                    "description": "The old string to replace. Can be multi-line.",
                                    "type": "string",
                                },
                                "new": {
                                    "description": "The new string to replace with. Can be multi-line.",
                                    "type": "string",
                                },
                                "replace_all": {
                                    "default": False,
                                    "description": "Whether to replace all occurrences.",
                                    "type": "boolean",
                                },
                            },
                            "required": ["old", "new"],
                            "type": "object",
                        },
                        {
                            "items": {
                                "description": """\
编辑操作参数。

Attributes:
    old: 要替换的旧字符串，可以是多行文本。
    new: 替换后的新字符串，可以是多行文本。
    replace_all: 是否替换所有匹配项，默认只替换第一个匹配项。\
""",
                                "properties": {
                                    "old": {
                                        "description": "The old string to replace. Can be multi-line.",
                                        "type": "string",
                                    },
                                    "new": {
                                        "description": "The new string to replace with. Can be multi-line.",
                                        "type": "string",
                                    },
                                    "replace_all": {
                                        "default": False,
                                        "description": "Whether to replace all occurrences.",
                                        "type": "boolean",
                                    },
                                },
                                "required": ["old", "new"],
                                "type": "object",
                            },
                            "type": "array",
                        },
                    ],
                    "description": "The edit(s) to apply to the file. You can provide a single edit or a list of edits here.",
                },
            },
            "required": ["path", "edit"],
            "type": "object",
        }
    )


def test_search_web_params_schema(search_web_tool: SearchWeb):
    """Test the schema of MoonshotSearch tool parameters."""
    assert search_web_tool.base.parameters == snapshot(
        {
            "description": """\
Web 搜索参数。

Attributes:
    query: 搜索查询文本。
    limit: 返回结果数量，默认为 5。
    include_content: 是否包含网页内容，开启后可能消耗大量 token。\
""",
            "properties": {
                "query": {
                    "description": "The query text to search for.",
                    "type": "string",
                },
                "limit": {
                    "default": 5,
                    "description": "The number of results to return. Typically you do not need to set this value. When the results do not contain what you need, you probably want to give a more concrete query.",
                    "maximum": 20,
                    "minimum": 1,
                    "type": "integer",
                },
                "include_content": {
                    "default": False,
                    "description": "Whether to include the content of the web pages in the results. It can consume a large amount of tokens when this is set to True. You should avoid enabling this when `limit` is set to a large value.",
                    "type": "boolean",
                },
            },
            "required": ["query"],
            "type": "object",
        }
    )


def test_fetch_url_params_schema(fetch_url_tool: FetchURL):
    """Test the schema of FetchURL tool parameters."""
    assert fetch_url_tool.base.parameters == snapshot(
        {
            "description": """\
URL 获取参数。

Attributes:
    url: 要获取内容的 URL。\
""",
            "properties": {
                "url": {
                    "description": "The URL to fetch content from.",
                    "type": "string",
                }
            },
            "required": ["url"],
            "type": "object",
        }
    )
