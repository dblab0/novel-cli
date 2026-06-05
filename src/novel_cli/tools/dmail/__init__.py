"""D-Mail 发送工具模块。

提供发送 D-Mail 的工具功能，用于实现时间通信。
"""

from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnValue

from novel_cli.soul.denwarenji import DenwaRenji, DenwaRenjiError, DMail
from novel_cli.tools.utils import load_desc

NAME = "SendDMail"


class SendDMail(CallableTool2[DMail]):
    """发送 D-Mail 工具。

    用于发送 D-Mail（时间通信邮件）的工具。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型（DMail）。
    """

    name: str = NAME
    description: str = load_desc(Path(__file__).parent / "dmail.md")
    params: type[DMail] = DMail

    def __init__(self, denwa_renji: DenwaRenji) -> None:
        """初始化发送 D-Mail 工具。

        Args:
            denwa_renji: 电话连接实例，用于发送 D-Mail。
        """
        super().__init__()
        self._denwa_renji = denwa_renji

    @override
    async def __call__(self, params: DMail) -> ToolReturnValue:
        """执行发送 D-Mail 操作。

        Args:
            params: D-Mail 参数。

        Returns:
            ToolReturnValue 表示发送结果。

        Raises:
            不抛出异常，但会返回 ToolError 表示发送失败。
        """
        try:
            self._denwa_renji.send_dmail(params)
        except DenwaRenjiError as e:
            return ToolError(
                output="",
                message=f"Failed to send D-Mail. Error: {str(e)}",
                brief="Failed to send D-Mail",
            )
        return ToolOk(
            output="",
            message=(
                "If you see this message, the D-Mail was NOT sent successfully. "
                "This may be because some other tool that needs approval was rejected."
            ),
            brief="El Psy Kongroo",
        )
