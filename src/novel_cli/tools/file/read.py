"""文件读取工具模块。

提供读取文本文件的能力，支持按行偏移、行数限制等方式读取文件内容。
"""

from collections import deque
from pathlib import Path
from typing import override

from kaos.path import KaosPath
from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field, model_validator

from novel_cli.soul.agent import Runtime
from novel_cli.tools.file.utils import MEDIA_SNIFF_BYTES, detect_file_type
from novel_cli.tools.utils import load_desc, truncate_line
from novel_cli.utils.path import is_within_workspace
from novel_cli.utils.sensitive import is_sensitive_file

MAX_LINES = 1000
MAX_LINE_LENGTH = 2000
MAX_BYTES = 100 << 10  # 100KB


class Params(BaseModel):
    """ReadFile 工具的参数模型。

    Attributes:
        path: 要读取的文件路径。读取工作目录外的文件时需要使用绝对路径。
        line_offset: 开始读取的行号。默认从文件开头读取。负值表示从文件末尾开始读取
            （例如 -100 读取最后 100 行）。负值的绝对值不能超过 MAX_LINES。
        n_lines: 要读取的行数。默认读取最多 MAX_LINES 行，这也是允许的最大值。
    """

    path: str = Field(
        description=(
            "The path to the file to read. Absolute paths are required when reading files "
            "outside the working directory."
        )
    )
    line_offset: int = Field(
        description=(
            "The line number to start reading from. "
            "By default read from the beginning of the file. "
            "Set this when the file is too large to read at once. "
            "Negative values read from the end of the file (e.g. -100 reads the last 100 lines). "
            f"The absolute value of negative offset cannot exceed {MAX_LINES}."
        ),
        default=1,
    )
    n_lines: int = Field(
        description=(
            "The number of lines to read. "
            f"By default read up to {MAX_LINES} lines, which is the max allowed value. "
            "Set this value when the file is too large to read at once."
        ),
        default=MAX_LINES,
        ge=1,
    )

    @model_validator(mode="after")
    def _validate_line_offset(self) -> "Params":
        """验证行偏移参数的有效性。

        Returns:
            验证后的 Params 实例。

        Raises:
            ValueError: 当 line_offset 为 0 或小于 -MAX_LINES 时抛出。
        """
        if self.line_offset == 0:
            raise ValueError(
                "line_offset cannot be 0; use 1 for the first line or -1 for the last line"
            )
        if self.line_offset < -MAX_LINES:
            raise ValueError(
                f"line_offset cannot be less than -{MAX_LINES}. "
                "Use a positive line_offset with the total line count "
                "to read from a specific position."
            )
        return self


class ReadFile(CallableTool2[Params]):
    """文件读取工具类。

    用于读取文本文件内容，支持正向和逆向（tail 模式）读取。

    Attributes:
        name: 工具名称。
        params: 参数类型。
    """

    name: str = "ReadFile"
    params: type[Params] = Params

    def __init__(self, runtime: Runtime) -> None:
        """初始化 ReadFile 工具实例。

        Args:
            runtime: 运行时环境对象。
        """
        description = load_desc(
            Path(__file__).parent / "read.md",
            {
                "MAX_LINES": MAX_LINES,
                "MAX_LINE_LENGTH": MAX_LINE_LENGTH,
                "MAX_BYTES": MAX_BYTES,
            },
        )
        super().__init__(description=description)
        self._runtime = runtime
        self._work_dir = runtime.builtin_args.NOVEL_WORK_DIR
        self._additional_dirs = runtime.additional_dirs

    async def _validate_path(self, path: KaosPath) -> ToolError | None:
        """验证路径是否可以安全读取。

        Args:
            path: 要验证的路径对象。

        Returns:
            如果路径无效则返回 ToolError，否则返回 None。
        """
        resolved_path = path.canonical()

        if (
            not is_within_workspace(resolved_path, self._work_dir, self._additional_dirs)
            and not path.is_absolute()
        ):
            # 工作目录外的文件只能通过绝对路径读取
            return ToolError(
                message=(
                    f"`{path}` is not an absolute path. "
                    "You must provide an absolute path to read a file "
                    "outside the working directory."
                ),
                brief="Invalid path",
            )
        return None

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行文件读取操作。

        Args:
            params: 读取参数。

        Returns:
            工具执行结果，包含文件内容或错误信息。
        """
        if not params.path:
            return ToolError(
                message="File path cannot be empty.",
                brief="Empty file path",
            )

        try:
            p = KaosPath(params.path).expanduser()
            if err := await self._validate_path(p):
                return err
            p = p.canonical()

            if is_sensitive_file(str(p)):
                return ToolError(
                    message=(
                        f"`{params.path}` appears to contain secrets "
                        "(matched sensitive file pattern). "
                        "Reading this file is blocked to protect credentials."
                    ),
                    brief="Sensitive file",
                )

            if not await p.exists():
                return ToolError(
                    message=f"`{params.path}` does not exist.",
                    brief="File not found",
                )
            if not await p.is_file():
                return ToolError(
                    message=f"`{params.path}` is not a file.",
                    brief="Invalid path",
                )

            header = await p.read_bytes(MEDIA_SNIFF_BYTES)
            file_type = detect_file_type(str(p), header=header)
            if file_type.kind in ("image", "video"):
                return ToolError(
                    message=(
                        f"`{params.path}` is a {file_type.kind} file. "
                        "Use other appropriate tools to read image or video files."
                    ),
                    brief="Unsupported file type",
                )

            if file_type.kind == "unknown":
                return ToolError(
                    message=(
                        f"`{params.path}` seems not readable. "
                        "You may need to read it with proper shell commands, Python tools "
                        "or MCP tools if available. "
                        "If you read/operate it with Python, you MUST ensure that any "
                        "third-party packages are installed in a virtual environment (venv)."
                    ),
                    brief="File not readable",
                )

            assert params.n_lines >= 1
            assert params.line_offset != 0

            if params.line_offset < 0:
                return await self._read_tail(p, params)
            else:
                return await self._read_forward(p, params)
        except Exception as e:
            return ToolError(
                message=f"Failed to read {params.path}. Error: {e}",
                brief="Failed to read file",
            )

    async def _read_forward(self, p: KaosPath, params: Params) -> ToolReturnValue:
        """从正向偏移位置读取文件，同时计算总行数。

        Args:
            p: 文件路径对象。
            params: 读取参数。

        Returns:
            工具执行结果，包含格式化的文件内容。
        """
        lines: list[str] = []
        n_bytes = 0
        truncated_line_numbers: list[int] = []
        max_lines_reached = False
        max_bytes_reached = False
        collecting = True  # 收集足够行数后置为 False
        current_line_no = 0
        async for line in p.read_lines(errors="replace"):
            current_line_no += 1
            if not collecting:
                continue
            if current_line_no < params.line_offset:
                continue
            truncated = truncate_line(line, MAX_LINE_LENGTH)
            if truncated != line:
                truncated_line_numbers.append(current_line_no)
            lines.append(truncated)
            n_bytes += len(truncated.encode("utf-8"))
            if len(lines) >= params.n_lines:
                collecting = False
            elif len(lines) >= MAX_LINES:
                max_lines_reached = True
                collecting = False
            elif n_bytes >= MAX_BYTES:
                max_bytes_reached = True
                collecting = False

        total_lines = current_line_no

        # 格式化输出，添加行号（类似 cat -n）
        start_line = params.line_offset
        lines_with_no: list[str] = []
        for line_num, line in zip(range(start_line, start_line + len(lines)), lines, strict=True):
            lines_with_no.append(f"{line_num:6d}\t{line}")

        message = (
            f"{len(lines)} lines read from file starting from line {start_line}."
            if len(lines) > 0
            else "No lines read from file."
        )
        message += f" Total lines in file: {total_lines}."
        if max_lines_reached:
            message += f" Max {MAX_LINES} lines reached."
        elif max_bytes_reached:
            message += f" Max {MAX_BYTES} bytes reached."
        elif len(lines) < params.n_lines:
            message += " End of file reached."
        if truncated_line_numbers:
            message += f" Lines {truncated_line_numbers} were truncated."
        return ToolOk(
            output="".join(lines_with_no),
            message=message,
        )

    async def _read_tail(self, p: KaosPath, params: Params) -> ToolReturnValue:
        """从负向偏移位置读取文件（tail 模式）。

        Args:
            p: 文件路径对象。
            params: 读取参数。

        Returns:
            工具执行结果，包含格式化的文件内容。
        """
        tail_count = abs(params.line_offset)

        # 使用双端队列保存最后 `tail_count` 行及其行号
        # 每个条目格式：(行号, 截断后的行内容, 是否被截断)
        tail_buf: deque[tuple[int, str, bool]] = deque(maxlen=tail_count)
        current_line_no = 0
        async for line in p.read_lines(errors="replace"):
            current_line_no += 1
            truncated = truncate_line(line, MAX_LINE_LENGTH)
            tail_buf.append((current_line_no, truncated, truncated != line))

        total_lines = current_line_no

        # 第一步：从 tail_buf 头部应用 n_lines / MAX_LINES 限制
        # 这保留了用户请求的起始位置
        all_entries = list(tail_buf)
        line_limit = min(params.n_lines, MAX_LINES)
        candidates = all_entries[:line_limit]
        max_lines_reached = len(all_entries) > MAX_LINES and len(candidates) == MAX_LINES

        # 第二步：应用 MAX_BYTES 限制 —— 如果候选行超出字节预算，
        # 反向扫描保留最新的（最接近文件末尾的）符合条件的行
        total_candidate_bytes = sum(len(entry[1].encode("utf-8")) for entry in candidates)
        if total_candidate_bytes > MAX_BYTES:
            max_bytes_reached = True
            kept = 0
            n_bytes = 0
            for entry in reversed(candidates):
                n_bytes += len(entry[1].encode("utf-8"))
                if n_bytes > MAX_BYTES:
                    break
                kept += 1
            candidates = candidates[len(candidates) - kept :]
        else:
            max_bytes_reached = False

        # 第三步：从候选行中收集结果
        lines: list[str] = []
        line_numbers: list[int] = []
        truncated_line_numbers: list[int] = []

        for line_no, truncated, was_truncated in candidates:
            if was_truncated:
                truncated_line_numbers.append(line_no)
            lines.append(truncated)
            line_numbers.append(line_no)

        # 格式化输出，使用绝对行号
        lines_with_no: list[str] = []
        for line_num, line in zip(line_numbers, lines, strict=True):
            lines_with_no.append(f"{line_num:6d}\t{line}")

        start_line = line_numbers[0] if line_numbers else total_lines + 1
        message = (
            f"{len(lines)} lines read from file starting from line {start_line}."
            if len(lines) > 0
            else "No lines read from file."
        )
        message += f" Total lines in file: {total_lines}."
        if max_lines_reached:
            message += f" Max {MAX_LINES} lines reached."
        elif max_bytes_reached:
            message += f" Max {MAX_BYTES} bytes reached."
        elif len(lines) < params.n_lines:
            message += " End of file reached."
        if truncated_line_numbers:
            message += f" Lines {truncated_line_numbers} were truncated."
        return ToolOk(
            output="".join(lines_with_no),
            message=message,
        )
