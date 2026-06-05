"""智能体工具模块。

提供启动和管理子智能体的能力，支持前台和后台运行模式。
"""

import asyncio
from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolError, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.soul.agent import Runtime
from novel_cli.soul.toolset import get_current_tool_call_or_none
from novel_cli.subagents.models import AgentLaunchSpec, AgentTypeDefinition
from novel_cli.subagents.runner import ForegroundRunRequest, ForegroundSubagentRunner
from novel_cli.tools.utils import load_desc
from novel_cli.utils.logging import logger

NAME = "Agent"

MAX_FOREGROUND_TIMEOUT = 60 * 60  # 1 hour
MAX_BACKGROUND_TIMEOUT = 60 * 60  # 1 hour


class Params(BaseModel):
    """Agent 工具的参数模型。

    Attributes:
        description: 任务的简短描述（3-5 个词）。
        prompt: 智能体要执行的任务描述。
        subagent_type: 内置智能体类型，默认为 `coder`。
        model: 可选的模型覆盖。选择优先级：此参数 > 内置类型默认模型 > 父智能体当前模型。
        resume: 可选，要恢复的智能体实例 ID，而不是创建新实例。
        run_in_background: 是否在后台运行智能体。除非任务可以独立继续且提前返回控制权
            有明显好处，否则应优先使用前台模式。
        timeout: 智能体任务的超时时间（秒）。前台模式无默认超时（运行至完成），
            最大 3600 秒（1 小时）。后台模式默认使用配置值（15 分钟），
            最大 3600 秒（1 小时）。超时后智能体将被停止。
    """

    description: str = Field(description="A short (3-5 word) description of the task")
    prompt: str = Field(description="The task for the agent to perform")
    subagent_type: str = Field(
        default="coder",
        description="The built-in agent type to use. Defaults to `coder`.",
    )
    model: str | None = Field(
        default=None,
        description=(
            "Optional model override. Selection priority is: this parameter, then the built-in "
            "type default model, then the parent agent's current model."
        ),
    )
    resume: str | None = Field(
        default=None,
        description="Optional agent ID to resume instead of creating a new instance.",
    )
    run_in_background: bool = Field(
        default=False,
        description=(
            "Whether to run the agent in the background. Prefer false unless the task can "
            "continue independently and there is a clear benefit to returning control before "
            "the result is needed."
        ),
    )
    timeout: int | None = Field(
        default=None,
        description=(
            "Timeout in seconds for the agent task. "
            "Foreground: no default timeout (runs until completion), max 3600s (1hr). "
            "Background: default from config (15min), max 3600s (1hr). "
            "The agent is stopped if it exceeds this limit."
        ),
        ge=30,
        le=MAX_BACKGROUND_TIMEOUT,
    )

    @property
    def effective_timeout(self) -> int | None:
        """返回用户指定的超时时间，或 None 表示使用系统默认值。"""
        return self.timeout


class AgentTool(CallableTool2[Params]):
    """智能体工具类。

    用于启动和管理子智能体，支持前台执行和后台运行两种模式。

    Attributes:
        name: 工具名称。
        params: 参数类型。
    """

    name: str = NAME
    params: type[Params] = Params

    def __init__(self, runtime: Runtime):
        """初始化 AgentTool 实例。

        Args:
            runtime: 运行时环境对象。
        """
        super().__init__(
            description=load_desc(
                Path(__file__).parent / "description.md",
                {
                    "BUILTIN_AGENT_TYPES_MD": self._builtin_type_lines(runtime),
                },
            )
        )
        self._runtime = runtime

    @staticmethod
    def _builtin_type_lines(runtime: Runtime) -> str:
        """生成内置智能体类型的描述文本。

        Args:
            runtime: 运行时环境对象。

        Returns:
            格式化的内置类型描述字符串。
        """
        lines: list[str] = []
        for name, type_def in runtime.labor_market.builtin_types.items():
            tool_names = AgentTool._tool_summary(type_def)
            model = type_def.default_model or "inherit"
            suffix = (
                f" When to use: {AgentTool._normalize_summary(type_def.when_to_use)}"
                if type_def.when_to_use
                else ""
            )
            background = "yes" if type_def.supports_background else "no"
            lines.append(
                f"- `{name}`: {type_def.description} "
                f"(Tools: {tool_names}, Model: {model}, Background: {background}).{suffix}"
            )
        return "\n".join(lines)

    @staticmethod
    def _normalize_summary(text: str) -> str:
        """规范化摘要文本，移除多余空白。

        Args:
            text: 原始文本。

        Returns:
            规范化后的文本。
        """
        return " ".join(text.split())

    @staticmethod
    def _tool_summary(type_def: AgentTypeDefinition) -> str:
        """生成工具策略摘要。

        Args:
            type_def: 智能体类型定义。

        Returns:
            工具名称列表或通配符。
        """
        if type_def.tool_policy.mode != "allowlist":
            return "*"
        if not type_def.tool_policy.tools:
            return "(none)"
        return ", ".join(AgentTool._unique_tool_names(type_def.tool_policy.tools))

    @staticmethod
    def _unique_tool_names(tool_paths: tuple[str, ...]) -> list[str]:
        """从工具路径中提取唯一的工具名称。

        Args:
            tool_paths: 工具路径元组。

        Returns:
            唯一工具名称列表。
        """
        names: list[str] = []
        for path in tool_paths:
            name = path.split(":")[-1]
            if name not in names:
                names.append(name)
        return names

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行智能体工具调用。

        Args:
            params: 工具参数。

        Returns:
            工具执行结果。
        """
        if self._runtime.role != "root":
            return ToolError(
                message="Subagents cannot launch other subagents.",
                brief="Agent unavailable",
            )
        if params.model is not None and params.model not in self._runtime.config.models:
            return ToolError(
                message=f"Unknown model alias: {params.model}",
                brief="Invalid model alias",
            )
        if params.run_in_background:
            return await self._run_in_background(params)
        timeout = params.effective_timeout
        try:
            runner = ForegroundSubagentRunner(self._runtime)
            req = ForegroundRunRequest(
                description=params.description,
                prompt=params.prompt,
                requested_type=params.subagent_type or "coder",
                model=params.model,
                resume=params.resume,
            )
            if timeout is not None:
                return await asyncio.wait_for(runner.run(req), timeout=timeout)
            return await runner.run(req)
        except TimeoutError as exc:
            # 注意：来自 run_soul 内部的 TimeoutError（如 aiohttp）现在会被
            # run_soul_checked 捕获并转换为 SoulRunFailure。此处理器主要覆盖
            # wait_for 的任务级超时和 run_soul 之前的 TimeoutError。
            if isinstance(exc.__cause__, asyncio.CancelledError):
                logger.warning("Foreground agent timed out after {t}s", t=timeout)
                return ToolError(
                    message=f"Agent timed out after {timeout}s.",
                    brief=f"Agent timed out ({timeout}s)",
                )
            # 内部超时（如 aiohttp 请求）——视为通用失败
            logger.exception("Foreground agent run failed")
            return ToolError(message=f"Failed to run agent: {exc}", brief="Agent failed")
        except Exception as exc:
            logger.exception("Foreground agent run failed")
            return ToolError(message=f"Failed to run agent: {exc}", brief="Agent failed")

    async def _run_in_background(self, params: Params) -> ToolReturnValue:
        """在后台运行智能体任务。

        Args:
            params: 工具参数。

        Returns:
            工具执行结果。
        """
        assert self._runtime.subagent_store is not None
        try:
            tool_call = get_current_tool_call_or_none()
            if tool_call is None:
                return ToolError(
                    message="Background agent requires a tool call context.",
                    brief="No tool call context",
                )

            requested_type = params.subagent_type or "coder"
            if params.resume:
                record = self._runtime.subagent_store.require_instance(params.resume)
                if record.status in {"running_foreground", "running_background"}:
                    return ToolError(
                        message=(
                            f"Agent instance {record.agent_id} is still {record.status} and cannot "
                            "be resumed concurrently."
                        ),
                        brief="Agent already running",
                    )
                actual_type = record.subagent_type
                agent_id = record.agent_id
                # 验证恢复实例的有效模型 —— 存储在 launch_spec 中的模型
                # 可能在实例创建后从配置中移除。params.model 已在 __call__ 中验证，
                # 所以这里只检查存储的 effective_model 回退值。
                if params.model is None:
                    type_def = self._runtime.labor_market.require_builtin_type(actual_type)
                    effective = record.launch_spec.effective_model or type_def.default_model
                    if effective is not None and effective not in self._runtime.config.models:
                        return ToolError(
                            message=f"Unknown model alias: {effective}",
                            brief="Invalid model alias",
                        )
            else:
                actual_type = requested_type
                import uuid

                agent_id = f"a{uuid.uuid4().hex[:8]}"
                record = None

            created_instance = False
            if not params.resume:
                type_def = self._runtime.labor_market.require_builtin_type(actual_type)
                self._runtime.subagent_store.create_instance(
                    agent_id=agent_id,
                    description=params.description.strip(),
                    launch_spec=AgentLaunchSpec(
                        agent_id=agent_id,
                        subagent_type=actual_type,
                        model_override=params.model,
                        effective_model=params.model or type_def.default_model,
                    ),
                )
                created_instance = True

            # 在分发异步任务前同步标记 running_background 状态，
            # 以便并发恢复尝试能立即看到保护状态
            # （asyncio.create_task 只是将协程加入队列）
            self._runtime.subagent_store.update_instance(
                agent_id,
                status="running_background",
            )
            try:
                view = self._runtime.background_tasks.create_agent_task(
                    agent_id=agent_id,
                    subagent_type=actual_type,
                    prompt=params.prompt,
                    description=params.description.strip(),
                    tool_call_id=tool_call.id,
                    model_override=params.model,
                    timeout_s=params.effective_timeout,
                    resumed=params.resume is not None,
                )
            except Exception:
                self._runtime.subagent_store.update_instance(
                    agent_id,
                    status="idle",
                )
                if created_instance:
                    self._runtime.subagent_store.delete_instance(agent_id)
                raise
            lines = [
                f"task_id: {view.spec.id}",
                f"kind: {view.spec.kind}",
                f"status: {view.runtime.status}",
                f"description: {view.spec.description}",
                f"agent_id: {agent_id}",
                f"actual_subagent_type: {actual_type}",
                "automatic_notification: true",
                "next_step: You will be automatically notified when it completes.",
                (
                    "next_step: Use TaskOutput with this task_id for a non-blocking status/output "
                    "snapshot. Only set block=true when you intentionally want to wait."
                ),
                f'resume_hint: Use Agent(resume="{agent_id}", prompt="...") to continue this '
                "instance later.",
            ]
            return ToolReturnValue(
                is_error=False,
                output="\n".join(lines),
                message="Background task started.",
                display=[],
            )
        except FileNotFoundError as exc:
            return ToolError(message=str(exc), brief="Agent not found")
        except KeyError as exc:
            return ToolError(message=str(exc), brief="Invalid subagent type")
        except RuntimeError as exc:
            logger.exception("Background agent launch failed")
            return ToolError(message=str(exc), brief="Background start failed")


Agent = AgentTool
