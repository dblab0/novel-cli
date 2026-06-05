"""本地版本的 Grep 工具，使用 ripgrep 进行文件内容搜索。

注意事项：本实现不使用 KaosPath。
"""

import asyncio
import os
import platform
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import override

import aiohttp
from kosong.tooling import CallableTool2, ToolError, ToolReturnValue
from pydantic import BaseModel, Field

import novel_cli
from novel_cli.share import get_share_dir
from novel_cli.tools.utils import ToolResultBuilder, load_desc
from novel_cli.utils.aiohttp import new_client_session
from novel_cli.utils.logging import logger
from novel_cli.utils.sensitive import is_sensitive_file, sensitive_file_warning


class Params(BaseModel):
    """Grep 工具的参数模型。

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
        include_ignored: 是否包含被 .gitignore 等忽略规则排除的文件。
    """

    pattern: str = Field(
        description="The regular expression pattern to search for in file contents"
    )
    path: str = Field(
        description=(
            "File or directory to search in. Defaults to current working directory. "
            "If specified, it must be an absolute path."
        ),
        default=".",
    )
    glob: str | None = Field(
        description=(
            "Glob pattern to filter files (e.g. `*.js`, `*.{ts,tsx}`). No filter by default."
        ),
        default=None,
    )
    output_mode: str = Field(
        description=(
            "`content`: Show matching lines (supports `-B`, `-A`, `-C`, `-n`, `head_limit`); "
            "`files_with_matches`: Show file paths (supports `head_limit`); "
            "`count_matches`: Show total number of matches. "
            "Defaults to `files_with_matches`."
        ),
        default="files_with_matches",
    )
    before_context: int | None = Field(
        alias="-B",
        description=(
            "Number of lines to show before each match (the `-B` option). "
            "Requires `output_mode` to be `content`."
        ),
        default=None,
    )
    after_context: int | None = Field(
        alias="-A",
        description=(
            "Number of lines to show after each match (the `-A` option). "
            "Requires `output_mode` to be `content`."
        ),
        default=None,
    )
    context: int | None = Field(
        alias="-C",
        description=(
            "Number of lines to show before and after each match (the `-C` option). "
            "Requires `output_mode` to be `content`."
        ),
        default=None,
    )
    line_number: bool = Field(
        alias="-n",
        description=(
            "Show line numbers in output (the `-n` option). "
            "Requires `output_mode` to be `content`. Defaults to true."
        ),
        default=True,
    )
    ignore_case: bool = Field(
        alias="-i",
        description="Case insensitive search (the `-i` option).",
        default=False,
    )
    type: str | None = Field(
        description=(
            "File type to search. Examples: py, rust, js, ts, go, java, etc. "
            "More efficient than `glob` for standard file types."
        ),
        default=None,
    )
    head_limit: int | None = Field(
        description=(
            "Limit output to first N lines/entries, equivalent to `| head -N`. "
            "Works across all output modes: content (limits output lines), "
            "files_with_matches (limits file paths), count_matches (limits count entries). "
            "Defaults to 250. "
            "Pass 0 for unlimited (use sparingly — large result sets waste context)."
        ),
        default=250,
        ge=0,
    )
    offset: int = Field(
        description=(
            "Skip first N lines/entries before applying head_limit, "
            "equivalent to `| tail -n +N | head -N`. "
            "Works across all output modes. Defaults to 0."
        ),
        default=0,
        ge=0,
    )
    multiline: bool = Field(
        description=(
            "Enable multiline mode where `.` matches newlines and patterns can span "
            "lines (the `-U` and `--multiline-dotall` options). "
            "By default, multiline mode is disabled."
        ),
        default=False,
    )
    include_ignored: bool = Field(
        description=(
            "Include files that are ignored by `.gitignore`, `.ignore`, and other ignore "
            "rules. Useful for searching gitignored artifacts such as build outputs "
            "(e.g. `dist/`, `build/`) or `node_modules`. Sensitive files (like `.env`) "
            "remain filtered by the sensitive-file protection layer. Defaults to false."
        ),
        default=False,
    )


# ripgrep 版本号
RG_VERSION = "15.0.0"
# ripgrep 下载基础 URL
RG_BASE_URL = "http://cdn.novel-cli.dev/binaries/novel-cli/rg"
# ripgrep 执行超时时间（秒）
RG_TIMEOUT = 20  # seconds
# stdout/stderr 缓冲区大小上限（20MB）
RG_MAX_BUFFER = 20_000_000  # 20MB stdout/stderr buffer limit
# 进程终止宽限期（秒）：SIGTERM → SIGKILL
RG_KILL_GRACE = 5  # seconds: SIGTERM → SIGKILL
# ripgrep 下载锁，防止并发下载
_RG_DOWNLOAD_LOCK = asyncio.Lock()


def _rg_binary_name() -> str:
    """获取 ripgrep 二进制文件名。

    根据操作系统返回对应的二进制文件名。Windows 系统返回 `rg.exe`，
    其他系统返回 `rg`。

    Returns:
        ripgrep 二进制文件名。
    """
    return "rg.exe" if platform.system() == "Windows" else "rg"


def _find_existing_rg(bin_name: str) -> Path | None:
    """查找已存在的 ripgrep 二进制文件。

    按以下顺序查找：
    1. 共享目录中的 bin 目录
    2. 本地依赖目录中的 bin 目录
    3. 系统 PATH 中的 rg 命令

    Args:
        bin_name: ripgrep 二进制文件名。

    Returns:
        找到的二进制文件路径，若未找到则返回 None。
    """
    # 检查共享目录
    share_bin = get_share_dir() / "bin" / bin_name
    if share_bin.is_file():
        return share_bin

    # 检查本地依赖目录
    assert novel_cli.__file__ is not None
    local_dep = Path(novel_cli.__file__).parent / "deps" / "bin" / bin_name
    if local_dep.is_file():
        return local_dep

    # 检查系统 PATH
    system_rg = shutil.which("rg")
    if system_rg:
        return Path(system_rg)

    return None


def _detect_target() -> str | None:
    """检测当前系统的目标平台标识。

    根据操作系统和 CPU 架构返回 ripgrep 发布包的目标标识，
    如 `x86_64-apple-darwin`、`aarch64-unknown-linux-gnu` 等。

    Returns:
        目标平台标识字符串，若不支持则返回 None。
    """
    sys_name = platform.system()
    mach = platform.machine().lower()

    # 检测 CPU 架构
    if mach in ("x86_64", "amd64"):
        arch = "x86_64"
    elif mach in ("arm64", "aarch64"):
        arch = "aarch64"
    else:
        logger.error("Unsupported architecture for ripgrep: {mach}", mach=mach)
        return None

    # 检测操作系统
    if sys_name == "Darwin":
        os_name = "apple-darwin"
    elif sys_name == "Linux":
        os_name = "unknown-linux-musl" if arch == "x86_64" else "unknown-linux-gnu"
    elif sys_name == "Windows":
        os_name = "pc-windows-msvc"
    else:
        logger.error("Unsupported operating system for ripgrep: {sys_name}", sys_name=sys_name)
        return None

    return f"{arch}-{os_name}"


async def _download_and_install_rg(bin_name: str) -> Path:
    """下载并安装 ripgrep 二进制文件。

    从远程服务器下载对应平台的 ripgrep 发布包，解压并安装到共享目录。

    Args:
        bin_name: ripgrep 二进制文件名。

    Returns:
        安装后的二进制文件路径。

    Raises:
        RuntimeError: 平台不支持、下载失败或解压失败时抛出。
    """
    target = _detect_target()
    if not target:
        raise RuntimeError("Unsupported platform for ripgrep download")

    is_windows = "windows" in target
    archive_ext = "zip" if is_windows else "tar.gz"
    filename = f"ripgrep-{RG_VERSION}-{target}.{archive_ext}"
    url = f"{RG_BASE_URL}/{filename}"
    logger.info("Downloading ripgrep from {url}", url=url)

    share_bin_dir = get_share_dir() / "bin"
    share_bin_dir.mkdir(parents=True, exist_ok=True)
    destination = share_bin_dir / bin_name

    # 在受限网络环境下下载 ripgrep 二进制文件可能较慢
    download_timeout = aiohttp.ClientTimeout(total=600, sock_read=60, sock_connect=15)
    async with new_client_session(timeout=download_timeout) as session:
        with tempfile.TemporaryDirectory(prefix="novel-rg-") as tmpdir:
            tar_path = Path(tmpdir) / filename

            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    with open(tar_path, "wb") as fh:
                        async for chunk in resp.content.iter_chunked(1024 * 64):
                            if chunk:
                                fh.write(chunk)
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise RuntimeError("Failed to download ripgrep binary") from exc

            try:
                if is_windows:
                    # Windows 使用 zip 格式
                    with zipfile.ZipFile(tar_path, "r") as zf:
                        member_name = next(
                            (name for name in zf.namelist() if Path(name).name == bin_name),
                            None,
                        )
                        if not member_name:
                            raise RuntimeError("Ripgrep binary not found in archive")
                        with zf.open(member_name) as source, open(destination, "wb") as dest_fh:
                            shutil.copyfileobj(source, dest_fh)
                else:
                    # 其他系统使用 tar.gz 格式
                    with tarfile.open(tar_path, "r:gz") as tar:
                        member = next(
                            (m for m in tar.getmembers() if Path(m.name).name == bin_name),
                            None,
                        )
                        if not member:
                            raise RuntimeError("Ripgrep binary not found in archive")
                        extracted = tar.extractfile(member)
                        if not extracted:
                            raise RuntimeError("Failed to extract ripgrep binary")
                        with open(destination, "wb") as dest_fh:
                            shutil.copyfileobj(extracted, dest_fh)
            except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
                raise RuntimeError("Failed to extract ripgrep archive") from exc

    # 设置可执行权限
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    logger.info("Installed ripgrep to {destination}", destination=destination)
    return destination


async def _ensure_rg_path() -> str:
    """确保 ripgrep 二进制文件可用，返回其路径。

    若已存在则直接返回路径，否则下载安装后返回。

    Returns:
        ripgrep 二进制文件的绝对路径。

    Raises:
        RuntimeError: 下载或安装失败时抛出。
    """
    bin_name = _rg_binary_name()
    existing = _find_existing_rg(bin_name)
    if existing:
        return str(existing)

    # 使用锁防止并发下载
    async with _RG_DOWNLOAD_LOCK:
        existing = _find_existing_rg(bin_name)
        if existing:
            return str(existing)

        downloaded = await _download_and_install_rg(bin_name)
        return str(downloaded)


def _build_rg_args(rg_path: str, params: Params, *, single_threaded: bool = False) -> list[str]:
    """根据参数构建 ripgrep 命令行参数列表。

    Args:
        rg_path: ripgrep 二进制文件路径。
        params: Grep 工具参数。
        single_threaded: 是否使用单线程模式（用于解决 EAGAIN 错误）。

    Returns:
        ripgrep 命令行参数列表。
    """
    args: list[str] = [rg_path]

    # 固定参数
    if params.output_mode != "content":
        args.extend(["--max-columns", "500"])
    args.append("--hidden")
    if params.include_ignored:
        args.append("--no-ignore")
    # 排除版本控制目录
    for vcs_dir in (".git", ".svn", ".hg", ".bzr", ".jj", ".sl"):
        args.extend(["--glob", f"!{vcs_dir}"])

    if single_threaded:
        args.extend(["-j", "1"])

    # 搜索选项
    if params.ignore_case:
        args.append("--ignore-case")
    if params.multiline:
        args.extend(["--multiline", "--multiline-dotall"])

    # 内容显示选项（仅适用于 content 模式）
    if params.output_mode == "content":
        if params.before_context is not None:
            args.extend(["--before-context", str(params.before_context)])
        if params.after_context is not None:
            args.extend(["--after-context", str(params.after_context)])
        if params.context is not None:
            args.extend(["--context", str(params.context)])
        if params.line_number:
            args.append("--line-number")

    # 文件过滤选项
    if params.glob:
        args.extend(["--glob", params.glob])
    if params.type:
        args.extend(["--type", params.type])

    # 输出模式
    if params.output_mode == "files_with_matches":
        args.append("--files-with-matches")
    elif params.output_mode == "count_matches":
        args.append("--count-matches")

    # 使用 -- 分隔模式与标志，避免歧义（如模式以 - 开头）
    args.append("--")
    args.append(params.pattern)
    args.append(os.path.expanduser(params.path))

    return args


async def _read_stream(
    stream: asyncio.StreamReader,
    buffer: bytearray,
    limit: int,
    truncated_flag: list[bool] | None = None,
) -> bool:
    """增量读取流数据到缓冲区，最多读取 limit 字节。

    达到限制后继续清空管道（丢弃数据），防止子进程因管道缓冲区满而阻塞。

    Args:
        stream: 要读取的异步流。
        buffer: 用于存储数据的缓冲区。
        limit: 缓冲区大小上限（字节）。
        truncated_flag: 若提供，截断发生时 truncated_flag[0] 被设为 True。
            此标志在截断发生时同步设置，确保即使协程被 asyncio.wait_for
            超时取消，标志仍然可用。

    Returns:
        若输出被截断（超过限制）则返回 True。
    """
    truncated = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        if len(buffer) < limit:
            needed = limit - len(buffer)
            buffer.extend(chunk[:needed])
            if len(chunk) > needed:
                truncated = True
                if truncated_flag is not None:
                    truncated_flag[0] = True
        else:
            truncated = True
            if truncated_flag is not None:
                truncated_flag[0] = True
    return truncated


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    """两阶段终止进程：SIGTERM → 宽限期 → SIGKILL。

    Args:
        process: 要终止的异步子进程对象。
    """
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=RG_KILL_GRACE)
    except TimeoutError:
        process.kill()
        await process.wait()


def _is_eagain(stderr: str) -> bool:
    """检查 stderr 是否包含 EAGAIN 错误信息。

    Args:
        stderr: stderr 输出字符串。

    Returns:
        若包含 EAGAIN 错误则返回 True。
    """
    return "os error 11" in stderr or "Resource temporarily unavailable" in stderr


def _strip_path_prefix(output: str, search_base: str) -> str:
    """从每行输出中移除搜索基础路径前缀，生成相对路径。

    Args:
        output: ripgrep 输出字符串。
        search_base: 搜索基础路径。

    Returns:
        路径前缀被移除后的输出字符串。
    """
    prefix = search_base.rstrip("/\\") + os.sep
    return "\n".join(
        line[len(prefix) :] if line.startswith(prefix) else line for line in output.split("\n")
    )


class Grep(CallableTool2[Params]):
    """使用 ripgrep 进行文件内容搜索的工具。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "Grep"
    description: str = load_desc(Path(__file__).parent / "grep.md")
    params: type[Params] = Params

    @override
    async def __call__(self, params: Params, *, _retry: bool = False) -> ToolReturnValue:
        """执行 grep 搜索。

        Args:
            params: 搜索参数。
            _retry: 是否为重试调用（使用单线程模式解决 EAGAIN 错误）。

        Returns:
            工具执行结果。

        Raises:
            asyncio.CancelledError: 协程被取消时抛出。
        """
        try:
            builder = ToolResultBuilder()
            message = ""

            # 构建 rg 命令
            rg_path = await _ensure_rg_path()
            logger.debug("Using ripgrep binary: {rg_bin}", rg_bin=rg_path)
            args = _build_rg_args(rg_path, params, single_threaded=_retry)

            # 作为异步子进程执行搜索（非阻塞、可取消）
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # 增量读取 stdout/stderr，限制缓冲区大小
            stdout_buf = bytearray()
            stderr_buf = bytearray()
            timed_out = False
            stdout_truncated_flag: list[bool] = [False]

            try:
                assert process.stdout is not None
                assert process.stderr is not None
                await asyncio.wait_for(
                    asyncio.gather(
                        _read_stream(
                            process.stdout, stdout_buf, RG_MAX_BUFFER, stdout_truncated_flag
                        ),
                        _read_stream(process.stderr, stderr_buf, RG_MAX_BUFFER),
                    ),
                    timeout=RG_TIMEOUT,
                )
                await process.wait()
            except asyncio.CancelledError:
                await _kill_process(process)
                raise
            except TimeoutError:
                await _kill_process(process)
                timed_out = True

            output = stdout_buf.decode("utf-8", errors="replace")
            stderr_str = stderr_buf.decode("utf-8", errors="replace")

            # truncated_flag 在 _read_stream 内截断发生时同步设置，
            # 即使超时后仍然可用。
            buffer_truncated = stdout_truncated_flag[0]

            # 若缓冲区被截断，移除末尾不完整的行
            if buffer_truncated:
                last_nl = output.rfind("\n")
                output = output[:last_nl] if last_nl >= 0 else ""
                message = "Output exceeded buffer limit. Some results omitted."

            # 超时：若有部分结果则返回，否则报错
            if timed_out:
                if not output.strip():
                    return ToolError(
                        message=(
                            f"Grep timed out after {RG_TIMEOUT}s. "
                            "Try a more specific path or pattern."
                        ),
                        brief="Grep timed out",
                    )
                timeout_msg = f"Grep timed out after {RG_TIMEOUT}s. Partial results returned."
                message = f"{message} {timeout_msg}" if message else timeout_msg

            # rg 退出码：0=找到匹配，1=无匹配，2+=错误
            if not timed_out and process.returncode not in (0, 1):
                # EAGAIN：使用单线程模式重试一次
                if not _retry and _is_eagain(stderr_str):
                    logger.warning("rg EAGAIN error, retrying with -j 1")
                    return await self.__call__(params, _retry=True)
                return ToolError(
                    message=f"Failed to grep. Error: {stderr_str}",
                    brief="Failed to grep",
                )

            # --- 后处理流水线 ---

            # 步骤1：按修改时间排序（仅 files_with_matches 模式，超时时跳过）
            if not timed_out and params.output_mode == "files_with_matches":
                lines = [x for x in output.split("\n") if x.strip()]
                lines.sort(
                    key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
                    reverse=True,
                )
                output = "\n".join(lines)

            # 步骤2：缩短路径为相对路径（移除前缀）
            search_base = os.path.abspath(os.path.expanduser(params.path))
            if os.path.isfile(search_base):
                search_base = os.path.dirname(search_base)
            output = _strip_path_prefix(output, search_base)

            # 步骤3：从输出中过滤敏感文件
            # ripgrep 内容行正则：path:linenum:text（匹配行）
            # 或 path-linenum-text（上下文行）。分隔符为 `:` 或 `-`
            # 后跟数字再跟相同分隔符。
            _RG_LINE_RE = re.compile(r"^(.*?)([:\-])(\d+)\2")

            out_lines = output.split("\n")
            filtered_paths: list[str] = []
            kept_lines: list[str] = []
            sensitive_path_set: set[str] = set()
            for line in out_lines:
                if params.output_mode == "content":
                    # 匹配行格式："file.py:10:matched text"
                    # 上下文行格式："file.py-10-context text"
                    # 分隔符："--"
                    if line == "--":
                        kept_lines.append(line)
                        continue
                    m = _RG_LINE_RE.match(line)
                    file_path = m.group(1) if m else line
                elif params.output_mode == "count_matches":
                    # 计数行格式："file.py:42"
                    idx = line.rfind(":")
                    file_path = line[:idx] if idx > 0 else line
                else:
                    # files_with_matches：每行纯路径
                    file_path = line

                if file_path and is_sensitive_file(file_path):
                    if file_path not in sensitive_path_set:
                        sensitive_path_set.add(file_path)
                        filtered_paths.append(file_path)
                else:
                    kept_lines.append(line)

            if filtered_paths:
                # 移除过滤后遗留的末尾 "--" 分隔符
                while kept_lines and kept_lines[-1] == "--":
                    kept_lines.pop()
                output = "\n".join(kept_lines)
                warning = sensitive_file_warning(filtered_paths)
                message = f"{message} {warning}" if message else warning

            # 步骤4：count_matches 摘要（分页前，基于完整结果）
            lines = output.split("\n")
            if lines and lines[-1] == "":
                lines = lines[:-1]

            if params.output_mode == "count_matches":
                total_matches = 0
                total_files = 0
                for line in lines:
                    idx = line.rfind(":")
                    if idx > 0:
                        try:
                            total_matches += int(line[idx + 1 :])
                            total_files += 1
                        except ValueError:
                            pass
                count_summary = (
                    f"Found {total_matches} total occurrences across {total_files} files."
                )
                message = f"{message} {count_summary}" if message else count_summary

            # 步骤5：offset + head_limit 分页
            if params.offset > 0:
                lines = lines[params.offset :]

            effective_limit = params.head_limit
            if effective_limit and len(lines) > effective_limit:
                total = len(lines) + params.offset
                lines = lines[:effective_limit]
                output = "\n".join(lines)
                truncation_msg = (
                    f"Results truncated to {effective_limit} lines (total: {total}). "
                    f"Use offset={params.offset + effective_limit} to see more."
                )
                message = f"{message} {truncation_msg}" if message else truncation_msg
            else:
                output = "\n".join(lines)

            if not output and not buffer_truncated:
                no_match_msg = "No matches found"
                if message:
                    no_match_msg = f"{no_match_msg}. {message}"
                return builder.ok(message=no_match_msg)

            builder.write(output)
            return builder.ok(message=message)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            return ToolError(
                message=f"Failed to grep. Error: {str(e)}",
                brief="Failed to grep",
            )