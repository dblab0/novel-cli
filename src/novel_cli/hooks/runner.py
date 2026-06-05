"""Hook 执行器模块，提供单个钩子命令的执行和结果处理功能。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from novel_cli import logger


@dataclass
class HookResult:
    """单个钩子执行的结果。

    Attributes:
        action: 执行决策，"allow" 表示允许继续，"block" 表示阻断操作。
        reason: 阻断原因描述，仅在 action 为 "block" 时有意义。
        stdout: 命令的标准输出内容。
        stderr: 命令的标准错误输出内容。
        exit_code: 命令的退出码，0 表示成功，2 表示阻断。
        timed_out: 是否超时。
    """

    action: Literal["allow", "block"] = "allow"
    reason: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False


async def run_hook(
    command: str,
    input_data: dict[str, Any],
    *,
    timeout: int = 30,
    cwd: str | None = None,
) -> HookResult:
    """执行单个钩子命令。采用 fail-open 策略：错误/超时 -> allow。

    钩子命令通过 stdin 接收 JSON 输入，通过 stdout 返回结果。
    退出码含义：
    - 0: 成功，stdout 为空或 JSON 格式的决策
    - 2: 阻断操作，stderr 包含阻断原因

    Args:
        command: 要执行的 Shell 命令。
        input_data: 通过 stdin 传递给命令的 JSON 数据。
        timeout: 执行超时时间（秒），默认 30。
        cwd: 命令执行的工作目录，None 表示当前目录。

    Returns:
        HookResult 对象，包含执行决策和输出信息。
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=json.dumps(input_data).encode()),
                timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("Hook timed out after {}s: {}", timeout, command)
            return HookResult(action="allow", timed_out=True)
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise
    except Exception as e:
        logger.warning("Hook failed: {}: {}", command, e)
        return HookResult(action="allow", stderr=str(e))

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    exit_code = proc.returncode or 0

    # 退出码 2 表示阻断
    if exit_code == 2:
        return HookResult(
            action="block",
            reason=stderr.strip(),
            stdout=stdout,
            stderr=stderr,
            exit_code=2,
        )

    # 退出码 0 + JSON stdout 表示结构化决策
    if exit_code == 0 and stdout.strip():
        try:
            raw = json.loads(stdout)
            if isinstance(raw, dict):
                parsed = cast(dict[str, Any], raw)
                hook_output = cast(dict[str, Any], parsed.get("hookSpecificOutput", {}))
                if hook_output.get("permissionDecision") == "deny":
                    return HookResult(
                        action="block",
                        reason=str(hook_output.get("permissionDecisionReason", "")),
                        stdout=stdout,
                        stderr=stderr,
                        exit_code=0,
                    )
        except (json.JSONDecodeError, TypeError):
            pass

    return HookResult(action="allow", stdout=stdout, stderr=stderr, exit_code=exit_code)