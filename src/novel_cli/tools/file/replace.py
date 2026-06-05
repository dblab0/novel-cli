"""文件字符串替换工具实现。

提供 StrReplaceFile 工具，用于在文件中进行字符串替换操作，
支持单个替换和批量替换。
"""

from collections.abc import Callable
from pathlib import Path
from typing import override

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

_BASE_DESCRIPTION = load_desc(Path(__file__).parent / "replace.md")


class Edit(BaseModel):
    """编辑操作参数。

    Attributes:
        old: 要替换的旧字符串，可以是多行文本。
        new: 替换后的新字符串，可以是多行文本。
        replace_all: 是否替换所有匹配项，默认只替换第一个匹配项。
    """

    old: str = Field(description="The old string to replace. Can be multi-line.")
    new: str = Field(description="The new string to replace with. Can be multi-line.")
    replace_all: bool = Field(description="Whether to replace all occurrences.", default=False)


class Params(BaseModel):
    """文件替换参数。

    Attributes:
        path: 文件路径。在工作目录外编辑文件时需要提供绝对路径。
        edit: 编辑操作，可以是单个编辑或编辑列表。
    """

    path: str = Field(
        description=(
            "The path to the file to edit. Absolute paths are required when editing files "
            "outside the working directory."
        )
    )
    edit: Edit | list[Edit] = Field(
        description=(
            "The edit(s) to apply to the file. "
            "You can provide a single edit or a list of edits here."
        )
    )


class StrReplaceFile(CallableTool2[Params]):
    """文件字符串替换工具。

    用于在指定文件中进行字符串替换操作，支持精确替换和批量替换，
    并提供审批机制确保文件操作的安全性。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "StrReplaceFile"
    description: str = _BASE_DESCRIPTION
    params: type[Params] = Params

    def __init__(self, runtime: Runtime, approval: Approval):
        """初始化文件替换工具。

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
        """验证路径是否安全可编辑。

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
                    "You must provide an absolute path to edit a file "
                    "outside the working directory."
                ),
                brief="Invalid path",
            )
        return None

    def _apply_edit(self, content: str, edit: Edit) -> str:
        """应用单个编辑操作到内容。

        Args:
            content: 原始文件内容。
            edit: 编辑操作参数。

        Returns:
            编辑后的内容。
        """
        if edit.replace_all:
            return content.replace(edit.old, edit.new)
        else:
            return content.replace(edit.old, edit.new, 1)

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行文件替换操作。

        Args:
            params: 替换参数，包含路径和编辑操作。

        Returns:
            操作结果，成功或错误信息。
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

            plan_target = inspect_plan_edit_target(
                p,
                plan_mode_checker=self._plan_mode_checker,
                plan_file_path_getter=self._plan_file_path_getter,
            )
            if isinstance(plan_target, ToolError):
                return plan_target

            is_plan_file_edit = plan_target.is_plan_target

            if not await p.exists():
                if is_plan_file_edit:
                    return ToolError(
                        message=(
                            "The current plan file does not exist yet. "
                            "Use WriteFile to create it before calling StrReplaceFile."
                        ),
                        brief="Plan file not created",
                    )
                return ToolError(
                    message=f"`{params.path}` does not exist.",
                    brief="File not found",
                )
            if not await p.is_file():
                return ToolError(
                    message=f"`{params.path}` is not a file.",
                    brief="Invalid path",
                )

            # 读取文件内容
            content = await p.read_text(errors="replace")

            original_content = content
            edits = [params.edit] if isinstance(params.edit, Edit) else params.edit

            # 应用所有编辑操作
            for edit in edits:
                content = self._apply_edit(content, edit)

            # 检查是否有任何更改
            if content == original_content:
                return ToolError(
                    message="No replacements were made. The old string was not found in the file.",
                    brief="No replacements made",
                )

            diff_blocks: list[DisplayBlock] = await build_diff_blocks(
                str(p), original_content, content
            )

            action = (
                FileActions.EDIT
                if is_within_workspace(p, self._work_dir, self._additional_dirs)
                else FileActions.EDIT_OUTSIDE
            )

            # 计划文件编辑自动批准，其他编辑需要审批
            if not is_plan_file_edit:
                result = await self._approval.request(
                    self.name,
                    action,
                    f"Edit file `{p}`",
                    display=diff_blocks,
                )
                if not result:
                    return result.rejection_error()

            # 将修改后的内容写入文件
            await p.write_text(content, errors="replace")

            # 统计更改数量用于成功消息
            total_replacements = 0
            for edit in edits:
                if edit.replace_all:
                    total_replacements += original_content.count(edit.old)
                else:
                    total_replacements += 1 if edit.old in original_content else 0

            return ToolReturnValue(
                is_error=False,
                output="",
                message=(
                    f"File successfully edited. "
                    f"Applied {len(edits)} edit(s) with {total_replacements} total replacement(s)."
                ),
                display=diff_blocks,
            )

        except Exception as e:
            return ToolError(
                message=f"Failed to edit. Error: {e}",
                brief="Failed to edit file",
            )
