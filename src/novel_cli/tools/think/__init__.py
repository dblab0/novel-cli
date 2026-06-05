"""思考工具模块。

提供记录思考内容的工具功能。
"""

from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.tools.utils import load_desc


class Params(BaseModel):
    """思考工具参数。

    Attributes:
        thought: 要记录的思考内容。
    """

    thought: str = Field(description=("A thought to think about."))


class Think(CallableTool2[Params]):
    """思考工具。

    用于记录思考内容的工具。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "Think"
    description: str = load_desc(Path(__file__).parent / "think.md", {})
    params: type[Params] = Params

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行思考记录操作。

        Args:
            params: 包含思考内容的参数。

        Returns:
            表示思考已记录的 ToolReturnValue。
        """
        return ToolOk(output="", message="Thought logged")
