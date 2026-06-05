"""插件工具包装器模块。

将插件声明的工具作为子进程执行。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kosong.tooling import CallableTool, ToolError, ToolOk
from kosong.tooling.error import ToolRuntimeError
from loguru import logger

from novel_cli.plugin import PluginToolSpec
from novel_cli.tools.utils import ToolRejectedError
from novel_cli.utils.subprocess_env import get_clean_env
from novel_cli.wire.types import ToolReturnValue

if TYPE_CHECKING:
    from novel_cli.config import Config
    from novel_cli.soul.approval import Approval


def _get_host_values(config: Config) -> dict[str, str]:
    """从配置中提取当前主机值（api_key、base_url）。

    读取最新的 provider 凭据用于插件注入。

    Args:
        config: 配置对象。

    Returns:
        主机值映射字典。
    """
    from novel_cli.plugin.manager import collect_host_values

    return collect_host_values(config)


class PluginTool(CallableTool):
    """插件工具类，在子进程中执行插件命令。

    参数通过 stdin 以 JSON 格式传递，stdout 作为工具结果捕获。
    主机凭据在运行时注入为环境变量（而非写入配置文件），
    以处理凭据刷新。

    Attributes:
        _command: 执行命令列表。
        _plugin_dir: 插件目录路径。
        _inject: 注入映射（例如 {"novelCodeAPIKey": "api_key"}）。
        _config: 配置对象。
        _approval: 审批处理器，可选。
    """

    def __init__(
        self,
        tool_spec: PluginToolSpec,
        plugin_dir: Path,
        *,
        inject: dict[str, str],
        config: Config,
        approval: Approval | None = None,
        **kwargs: Any,
    ) -> None:
        """初始化插件工具。

        Args:
            tool_spec: 工具规范对象。
            plugin_dir: 插件目录路径。
            inject: 注入键映射。
            config: 配置对象。
            approval: 审批处理器，可选。
            **kwargs: 其他传递给基类的参数。
        """
        super().__init__(
            name=tool_spec.name,
            description=tool_spec.description,
            parameters=tool_spec.parameters or {"type": "object", "properties": {}},
            **kwargs,
        )
        self._command = tool_spec.command
        self._plugin_dir = plugin_dir
        self._inject = inject  # 例如 {"novelCodeAPIKey": "api_key"}
        self._config = config
        self._approval = approval

    def _build_env(self) -> dict[str, str]:
        """构建子进程环境变量，包含最新的主机凭据。"""
        env = get_clean_env()
        if self._inject:
            host_values = _get_host_values(self._config)
            for target_key, source_key in self._inject.items():
                if source_key in host_values:
                    # 使用插件的配置键名作为环境变量名注入
                    # 例如 novelCodeAPIKey=<fresh api_key>
                    env[target_key] = host_values[source_key]
        return env

    async def __call__(self, *args: Any, **kwargs: Any) -> ToolReturnValue:
        """执行插件工具。

        Args:
            *args: 未使用。
            **kwargs: 工具参数。

        Returns:
            工具返回值。
        """
        if self._approval is not None:
            description = f"Run plugin tool `{self.name}`."
            if not await self._approval.request(self.name, f"plugin:{self.name}", description):
                return ToolRejectedError()

        params_json = json.dumps(kwargs, ensure_ascii=False)

        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._plugin_dir),
                env=self._build_env(),
            )
        except Exception as exc:
            return ToolRuntimeError(str(exc))

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=params_json.encode("utf-8")),
                timeout=120,
            )
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolError(
                message=f"Plugin tool '{self.name}' timed out after 120s.",
                brief="Timeout",
            )

        output = stdout.decode("utf-8", errors="replace").strip()
        err_output = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            error_msg = err_output or output or f"Exit code {proc.returncode}"
            return ToolError(
                message=f"Plugin tool '{self.name}' failed: {error_msg}",
                brief=f"Exit {proc.returncode}",
            )

        if err_output:
            logger.debug("Plugin tool {name} stderr: {err}", name=self.name, err=err_output)

        return ToolOk(output=output)


def load_plugin_tools(
    plugins_dir: Path, config: Config, *, approval: Approval | None = None
) -> list[PluginTool]:
    """扫描已安装插件并创建 PluginTool 实例。

    Args:
        plugins_dir: 插件安装目录。
        config: 配置对象。
        approval: 审批处理器，可选。

    Returns:
        PluginTool 实例列表。
    """
    from novel_cli.plugin import PLUGIN_JSON, PluginError, parse_plugin_json

    if not plugins_dir.is_dir():
        return []

    tools: list[PluginTool] = []
    for child in sorted(plugins_dir.iterdir()):
        plugin_json = child / PLUGIN_JSON
        if not child.is_dir() or not plugin_json.is_file():
            continue
        try:
            spec = parse_plugin_json(plugin_json)
        except PluginError:
            continue
        for tool_spec in spec.tools:
            try:
                tool = PluginTool(
                    tool_spec,
                    plugin_dir=child,
                    inject=spec.inject,
                    config=config,
                    approval=approval,
                )
            except Exception:
                logger.warning(
                    "Skipping invalid plugin tool: {name} (from {plugin})",
                    name=tool_spec.name,
                    plugin=spec.name,
                )
                continue
            tools.append(tool)
            logger.info(
                "Loaded plugin tool: {name} (from {plugin})",
                name=tool_spec.name,
                plugin=spec.name,
            )
    return tools
