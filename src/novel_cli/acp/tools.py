"""ACP 工具替换模块。

本模块提供 ACP 工具的替换功能，将本地 Shell 工具替换为 ACP Terminal 工具以支持远程终端执行。
"""

import asyncio
from contextlib import suppress

import acp
from kaos import get_current_kaos
from kaos.local import local_kaos
from kosong.tooling import CallableTool2, ToolReturnValue

from novel_cli.soul.agent import Runtime
from novel_cli.soul.approval import Approval
from novel_cli.soul.toolset import NovelToolset
from novel_cli.tools.shell import Params as ShellParams
from novel_cli.tools.shell import Shell
from novel_cli.tools.utils import ToolResultBuilder
from novel_cli.wire.types import DisplayBlock


def replace_tools(
    client_capabilities: acp.schema.ClientCapabilities,
    acp_conn: acp.Client,
    acp_session_id: str,
    toolset: NovelToolset,
    runtime: Runtime,
) -> None:
    """替换工具集中的工具为 ACP 版本。

    仅在本地运行或 ACPKaos 下运行时替换工具。如果客户端支持终端功能，
    将 Shell 工具替换为 ACP Terminal 工具。

    Args:
        client_capabilities: ACP 客户端能力。
        acp_conn: ACP 客户端连接。
        acp_session_id: ACP 会话 ID。
        toolset: 工具集对象。
        runtime: 运行时对象。
    """
    current_kaos = get_current_kaos().name
    if current_kaos not in (local_kaos.name, "acp"):
        # 仅在本地运行或 ACPKaos 下时替换工具
        return

    if client_capabilities.terminal and (shell_tool := toolset.find(Shell)):
        # 如果支持，将 Shell 工具替换为 ACP Terminal 工具
        toolset.add(
            Terminal(
                shell_tool,
                acp_conn,
                acp_session_id,
                runtime.approval,
            )
        )


class HideOutputDisplayBlock(DisplayBlock):
    """指示输出应在 ACP 客户端中隐藏的特殊显示块。

    Attributes:
        type: 显示块类型标识符。
    """

    type: str = "acp/hide_output"


class Terminal(CallableTool2[ShellParams]):
    """ACP Terminal 工具类。

    将 Shell 工具替换为 ACP Terminal 工具，通过 ACP 客户端执行终端命令。

    Attributes:
        _acp_conn: ACP 客户端连接。
        _acp_session_id: ACP 会话 ID。
        _approval: 权限审批对象。
    """

    def __init__(
        self,
        shell_tool: Shell,
        acp_conn: acp.Client,
        acp_session_id: str,
        approval: Approval,
    ) -> None:
        """初始化 Terminal 工具。

        使用现有 Shell 工具的 name、description 和 params，
        这样当添加到工具集时，它会替换原来的 Shell 工具。

        Args:
            shell_tool: 现有的 Shell 工具。
            acp_conn: ACP 客户端连接。
            acp_session_id: ACP 会话 ID。
            approval: 权限审批对象。
        """
        super().__init__(shell_tool.name, shell_tool.description, shell_tool.params)
        self._acp_conn = acp_conn
        self._acp_session_id = acp_session_id
        self._approval = approval

    async def __call__(self, params: ShellParams) -> ToolReturnValue:
        """执行终端命令。

        Args:
            params: Shell 命令参数。

        Returns:
            工具返回值对象。
        """
        from novel_cli.acp.session import get_current_acp_tool_call_id_or_none

        builder = ToolResultBuilder()
        # 隐藏工具输出，因为使用 TerminalToolCallContent 已经直接将输出流式传输给用户
        builder.display(HideOutputDisplayBlock())

        if not params.command:
            return builder.error("Command cannot be empty.", brief="Empty command")

        approval_result = await self._approval.request(
            self.name,
            "run shell command",
            f"Run command `{params.command}`",
        )
        if not approval_result:
            return approval_result.rejection_error()

        timeout_seconds = float(params.timeout)
        timeout_label = f"{timeout_seconds:g}s"
        terminal_id: str | None = None
        exit_status: (
            acp.schema.WaitForTerminalExitResponse | acp.schema.TerminalExitStatus | None
        ) = None
        timed_out = False

        try:
            resp = await self._acp_conn.create_terminal(
                command=params.command,
                session_id=self._acp_session_id,
                output_byte_limit=builder.max_chars,
            )
            terminal_id = resp.terminal_id

            acp_tool_call_id = get_current_acp_tool_call_id_or_none()
            assert acp_tool_call_id, "Expected to have an ACP tool call ID in context"
            await self._acp_conn.session_update(
                session_id=self._acp_session_id,
                update=acp.schema.ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=acp_tool_call_id,
                    status="in_progress",
                    content=[
                        acp.schema.TerminalToolCallContent(
                            type="terminal",
                            terminal_id=terminal_id,
                        )
                    ],
                ),
            )

            try:
                async with asyncio.timeout(timeout_seconds):
                    exit_status = await self._acp_conn.wait_for_terminal_exit(
                        session_id=self._acp_session_id,
                        terminal_id=terminal_id,
                    )
            except TimeoutError:
                timed_out = True
                await self._acp_conn.kill_terminal(
                    session_id=self._acp_session_id,
                    terminal_id=terminal_id,
                )

            output_response = await self._acp_conn.terminal_output(
                session_id=self._acp_session_id,
                terminal_id=terminal_id,
            )
            builder.write(output_response.output)
            if output_response.exit_status:
                exit_status = output_response.exit_status

            exit_code = exit_status.exit_code if exit_status else None
            exit_signal = exit_status.signal if exit_status else None

            truncated_note = (
                " Output was truncated by the client output limit."
                if output_response.truncated
                else ""
            )

            if timed_out:
                return builder.error(
                    f"Command killed by timeout ({timeout_label}){truncated_note}",
                    brief=f"Killed by timeout ({timeout_label})",
                )
            if exit_signal:
                return builder.error(
                    f"Command terminated by signal: {exit_signal}.{truncated_note}",
                    brief=f"Signal: {exit_signal}",
                )
            if exit_code not in (None, 0):
                return builder.error(
                    f"Command failed with exit code: {exit_code}.{truncated_note}",
                    brief=f"Failed with exit code: {exit_code}",
                )
            return builder.ok(f"Command executed successfully.{truncated_note}")
        finally:
            if terminal_id is not None:
                with suppress(Exception):
                    await self._acp_conn.release_terminal(
                        session_id=self._acp_session_id,
                        terminal_id=terminal_id,
                    )