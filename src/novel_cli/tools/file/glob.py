"""Glob 工具实现。

提供 Glob 工具，用于在指定目录中搜索匹配 glob 模式的文件和目录。
"""

from pathlib import Path
from typing import override

from kaos.path import KaosPath
from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.soul.agent import Runtime
from novel_cli.tools.utils import load_desc
from novel_cli.utils.path import is_within_directory, is_within_workspace, list_directory

MAX_MATCHES = 1000


class Params(BaseModel):
    """Glob 搜索参数。

    Attributes:
        pattern: glob 模式，用于匹配文件或目录。
        directory: 搜索目录的绝对路径，默认为工作目录。
        include_dirs: 是否在结果中包含目录。
    """

    pattern: str = Field(description=("Glob pattern to match files/directories."))
    directory: str | None = Field(
        description=(
            "Absolute path to the directory to search in (defaults to working directory)."
        ),
        default=None,
    )
    include_dirs: bool = Field(
        description="Whether to include directories in results.",
        default=True,
    )


class Glob(CallableTool2[Params]):
    """Glob 搜索工具。

    用于在指定目录中搜索匹配 glob 模式的文件和目录，
    支持限制匹配数量以避免过大的搜索结果。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "Glob"
    description: str = load_desc(
        Path(__file__).parent / "glob.md",
        {
            "MAX_MATCHES": str(MAX_MATCHES),
        },
    )
    params: type[Params] = Params

    def __init__(self, runtime: Runtime) -> None:
        """初始化 Glob 工具。

        Args:
            runtime: 运行时环境，包含工作目录等信息。
        """
        super().__init__()
        self._work_dir = runtime.builtin_args.NOVEL_WORK_DIR
        self._additional_dirs = runtime.additional_dirs
        self._skills_dirs = runtime.skills_dirs

    async def _validate_pattern(self, pattern: str) -> ToolError | None:
        """验证 glob 模式是否安全可用。

        Args:
            pattern: 待验证的 glob 模式。

        Returns:
            如果模式不安全则返回 ToolError，否则返回 None。
        """
        if pattern.startswith("**"):
            ls_result = await list_directory(self._work_dir)
            return ToolError(
                output=ls_result,
                message=(
                    f"Pattern `{pattern}` starts with '**' which is not allowed. "
                    "This would recursively search all directories and may include large "
                    "directories like `node_modules`. Use more specific patterns instead. "
                    "For your convenience, a list of all files and directories in the "
                    "top level of the working directory is provided below."
                ),
                brief="Unsafe pattern",
            )
        return None

    async def _validate_directory(self, directory: KaosPath) -> ToolError | None:
        """验证目录是否安全可搜索。

        Args:
            directory: 待验证的目录路径。

        Returns:
            如果目录不安全则返回 ToolError，否则返回 None。
        """
        resolved_dir = directory.canonical()

        # 允许工作区内的目录（工作目录或附加目录）
        if is_within_workspace(resolved_dir, self._work_dir, self._additional_dirs):
            return None

        # 允许已发现的技能根目录内的目录
        if any(is_within_directory(resolved_dir, d) for d in self._skills_dirs):
            return None

        return ToolError(
            message=(
                f"`{directory}` is outside the workspace. "
                "You can only search within the working directory, "
                "additional directories, and skills directories."
            ),
            brief="Directory outside workspace",
        )

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行 Glob 搜索操作。

        Args:
            params: 搜索参数，包含模式和目录。

        Returns:
            操作结果，匹配的文件/目录列表或错误信息。
        """
        try:
            # 验证模式安全性
            pattern_error = await self._validate_pattern(params.pattern)
            if pattern_error:
                return pattern_error

            dir_path = (
                KaosPath(params.directory).expanduser() if params.directory else self._work_dir
            )

            if not dir_path.is_absolute():
                return ToolError(
                    message=(
                        f"`{params.directory}` is not an absolute path. "
                        "You must provide an absolute path to search."
                    ),
                    brief="Invalid directory",
                )

            # 验证目录安全性
            dir_error = await self._validate_directory(dir_path)
            if dir_error:
                return dir_error

            if not await dir_path.exists():
                return ToolError(
                    message=f"`{params.directory}` does not exist.",
                    brief="Directory not found",
                )
            if not await dir_path.is_dir():
                return ToolError(
                    message=f"`{params.directory}` is not a directory.",
                    brief="Invalid directory",
                )

            # 执行 glob 搜索 - 用户可在模式中直接使用 **
            matches: list[KaosPath] = []
            async for match in dir_path.glob(params.pattern):
                matches.append(match)

            # 如果不需要目录，则过滤掉
            if not params.include_dirs:
                matches = [p for p in matches if await p.is_file()]

            # 排序以保证输出一致性
            matches.sort()

            # 限制匹配数量
            message = (
                f"Found {len(matches)} matches for pattern `{params.pattern}`."
                if len(matches) > 0
                else f"No matches found for pattern `{params.pattern}`."
            )
            if len(matches) > MAX_MATCHES:
                matches = matches[:MAX_MATCHES]
                message += (
                    f" Only the first {MAX_MATCHES} matches are returned. "
                    "You may want to use a more specific pattern."
                )

            return ToolOk(
                output="\n".join(str(p.relative_to(dir_path)) for p in matches),
                message=message,
            )

        except Exception as e:
            return ToolError(
                message=f"Failed to search for pattern {params.pattern}. Error: {e}",
                brief="Glob failed",
            )
