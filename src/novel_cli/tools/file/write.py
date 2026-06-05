"""文件写入工具实现。

提供 WriteFile 工具，用于将内容写入文件，支持覆盖和追加两种模式。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, override

from kaos.path import KaosPath
from kosong.tooling import CallableTool2, ToolError, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.soul.agent import Runtime
from novel_cli.soul.approval import Approval
from novel_cli.tools.display import DisplayBlock
from novel_cli.tools.file import FileActions
from novel_cli.tools.file.plan_mode import inspect_plan_edit_target
from novel_cli.tools.utils import load_desc
from novel_cli.utils.diff import build_diff_blocks
from novel_cli.utils.path import is_within_workspace

_BASE_DESCRIPTION = load_desc(Path(__file__).parent / "write.md")


class Params(BaseModel):
    """文件写入参数。

    Attributes:
        path: 文件路径。在工作目录外写入文件时需要提供绝对路径。
        content: 要写入文件的内容。
        mode: 写入模式，支持 `overwrite`（覆盖整个文件）和 `append`（追加到文件末尾）。
    """

    path: str = Field(
        description=(
            "The path to the file to write. Absolute paths are required when writing files "
            "outside the working directory."
        )
    )
    content: str = Field(description="The content to write to the file")
    mode: Literal["overwrite", "append"] = Field(
        description=(
            "The mode to use to write to the file. "
            "Two modes are supported: `overwrite` for overwriting the whole file and "
            "`append` for appending to the end of an existing file."
        ),
        default="overwrite",
    )


class WriteFile(CallableTool2[Params]):
    """文件写入工具。

    用于将内容写入指定文件，支持覆盖和追加两种模式，
    并提供审批机制确保文件操作的安全性。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "WriteFile"
    description: str = _BASE_DESCRIPTION
    params: type[Params] = Params

    def __init__(self, runtime: Runtime, approval: Approval):
        """初始化文件写入工具。

        Args:
            runtime: 运行时环境，包含工作目录等信息。
            approval: 审批器，用于请求用户批准文件操作。
        """
        super().__init__()
        self._work_dir = runtime.builtin_args.NOVEL_WORK_DIR
        self._additional_dirs = runtime.additional_dirs
        self._approval = approval
        self._plan_mode_checker: Callable[[], bool] | None = None
        self._plan_file_path_getter: Callable[[], Path | None] | None = None

    def bind_plan_mode(
        self, checker: Callable[[], bool], path_getter: Callable[[], Path | None]
    ) -> None:
        """绑定计划模式状态检查器和计划文件路径获取器。

        Args:
            checker: 检查是否处于计划模式的函数。
            path_getter: 获取计划文件路径的函数。
        """
        self._plan_mode_checker = checker
        self._plan_file_path_getter = path_getter

    async def _validate_path(self, path: KaosPath) -> ToolError | None:
        """验证路径是否安全可写。

        Args:
            path: 待验证的路径。

        Returns:
            如果路径无效则返回 ToolError，否则返回 None。
        """
        resolved_path = path.canonical()

        if (
            not is_within_workspace(resolved_path, self._work_dir, self._additional_dirs)
            and not path.is_absolute()
        ):
            return ToolError(
                message=(
                    f"`{path}` is not an absolute path. "
                    "You must provide an absolute path to write a file "
                    "outside the working directory."
                ),
                brief="Invalid path",
            )
        return None

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行文件写入操作。

        Args:
            params: 写入参数，包含路径、内容和模式。

        Returns:
            操作结果，成功或错误信息。
        """
        # TODO: 检查路径是否可能包含敏感信息
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

            plan_target = inspect_plan_edit_target(
                p,
                plan_mode_checker=self._plan_mode_checker,
                plan_file_path_getter=self._plan_file_path_getter,
            )
            if isinstance(plan_target, ToolError):
                return plan_target

            is_plan_file_write = plan_target.is_plan_target
            if is_plan_file_write and plan_target.plan_path is not None:
                plan_target.plan_path.parent.mkdir(parents=True, exist_ok=True)

            if not await p.parent.exists():
                return ToolError(
                    message=f"`{params.path}` parent directory does not exist.",
                    brief="Parent directory not found",
                )

            # 验证写入模式参数
            if params.mode not in ["overwrite", "append"]:
                return ToolError(
                    message=(
                        f"Invalid write mode: `{params.mode}`. "
                        "Mode must be either `overwrite` or `append`."
                    ),
                    brief="Invalid write mode",
                )

            file_existed = await p.exists()
            old_text = None
            if file_existed:
                old_text = await p.read_text(errors="replace")

            new_text = (
                params.content if params.mode == "overwrite" else (old_text or "") + params.content
            )
            diff_blocks: list[DisplayBlock] = await build_diff_blocks(
                str(p),
                old_text or "",
                new_text,
            )

            # 计划文件写入自动批准，其他写入需要审批
            if not is_plan_file_write:
                action = (
                    FileActions.EDIT
                    if is_within_workspace(p, self._work_dir, self._additional_dirs)
                    else FileActions.EDIT_OUTSIDE
                )

                # 请求审批
                result = await self._approval.request(
                    self.name,
                    action,
                    f"Write file `{p}`",
                    display=diff_blocks,
                )
                if not result:
                    return result.rejection_error()

            # 将内容写入文件
            match params.mode:
                case "overwrite":
                    await p.write_text(params.content)
                case "append":
                    await p.append_text(params.content)

            # 获取文件信息用于成功消息
            file_size = (await p.stat()).st_size
            action = "overwritten" if params.mode == "overwrite" else "appended to"
            return ToolReturnValue(
                is_error=False,
                output="",
                message=(f"File successfully {action}. Current size: {file_size} bytes."),
                display=diff_blocks,
            )

        except Exception as e:
            return ToolError(
                message=f"Failed to write to {params.path}. Error: {e}",
                brief="Failed to write file",
            )
