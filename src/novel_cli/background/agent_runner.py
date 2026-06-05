"""后台 Agent 任务运行器模块。

提供后台 Agent 任务的执行管理，包括审批流程集成、输出写入和状态更新。
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from typing import TYPE_CHECKING

from novel_cli.approval_runtime import (
    ApprovalSource,
    reset_current_approval_source,
    set_current_approval_source,
)
from novel_cli.soul import RunCancelled
from novel_cli.subagents.builder import SubagentBuilder
from novel_cli.subagents.core import SubagentRunSpec, prepare_soul
from novel_cli.subagents.output import SubagentOutputWriter
from novel_cli.subagents.runner import run_with_summary_continuation
from novel_cli.utils.logging import logger
from novel_cli.wire import Wire

if TYPE_CHECKING:
    from novel_cli.approval_runtime.models import ApprovalRuntimeEvent
    from novel_cli.background.manager import BackgroundTaskManager
    from novel_cli.soul.agent import Runtime


class BackgroundAgentRunner:
    """后台 Agent 任务运行器。

    管理后台 Agent 任务的完整执行流程，包括审批订阅、输出写入、状态更新和异常处理。

    Attributes:
        _runtime: Agent Runtime 实例。
        _manager: 后台任务管理器。
        _task_id: 任务 ID。
        _agent_id: Agent ID。
        _subagent_type: 子 agent 类型。
        _prompt: 执行提示词。
        _model_override: 模型覆盖配置。
        _timeout_s: 超时时间（秒）。
        _resumed: 是否为恢复执行。
        _builder: 子 agent 构建器。
        _approval_update_tasks: 审批状态更新任务集合。
    """

    def __init__(
        self,
        *,
        runtime: Runtime,
        manager: BackgroundTaskManager,
        task_id: str,
        agent_id: str,
        subagent_type: str,
        prompt: str,
        model_override: str | None,
        timeout_s: int | None = None,
        resumed: bool = False,
    ) -> None:
        """初始化后台 Agent 运行器。

        Args:
            runtime: Agent Runtime 实例。
            manager: 后台任务管理器。
            task_id: 任务 ID。
            agent_id: Agent ID。
            subagent_type: 子 agent 类型。
            prompt: 执行提示词。
            model_override: 模型覆盖配置。
            timeout_s: 超时时间（秒）。
            resumed: 是否为恢复执行。
        """
        self._runtime = runtime
        self._manager = manager
        self._task_id = task_id
        self._agent_id = agent_id
        self._subagent_type = subagent_type
        self._prompt = prompt
        self._model_override = model_override
        self._timeout_s = timeout_s
        self._resumed = resumed
        self._builder = SubagentBuilder(runtime)
        self._approval_update_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        """执行后台 Agent 任务。

        处理审批订阅、超时控制、输出写入和异常捕获，并在结束时清理资源。
        """
        assert self._runtime.approval_runtime is not None
        assert self._runtime.subagent_store is not None
        token = set_current_approval_source(
            ApprovalSource(
                kind="background_agent",
                id=self._task_id,
                agent_id=self._agent_id,
                subagent_type=self._subagent_type,
            )
        )
        approval_subscription = self._runtime.approval_runtime.subscribe(
            self._on_approval_runtime_event
        )
        task_output_path = self._manager.store.output_path(self._task_id)
        output = SubagentOutputWriter(
            self._runtime.subagent_store.output_path(self._agent_id),
            extra_paths=[task_output_path],
        )

        try:
            if self._timeout_s is not None:
                await asyncio.wait_for(self._run_core(output), timeout=self._timeout_s)
            else:
                await self._run_core(output)
        except TimeoutError as exc:
            if isinstance(exc.__cause__, asyncio.CancelledError):
                # 任务级别超时（wait_from 从 CancelledError 构造 TimeoutError）
                logger.warning(
                    "Background agent task {id} timed out after {t}s",
                    id=self._task_id,
                    t=self._timeout_s,
                )
                self._runtime.subagent_store.update_instance(self._agent_id, status="failed")
                self._manager._mark_task_timed_out(
                    self._task_id, f"Agent task timed out after {self._timeout_s}s"
                )
                output.error(f"Agent task timed out after {self._timeout_s}s")
            else:
                # 内部超时（如 aiohttp 请求）——视为通用失败
                logger.exception("Background agent runner failed")
                self._runtime.subagent_store.update_instance(self._agent_id, status="failed")
                self._manager._mark_task_failed(self._task_id, str(exc))
                output.error(str(exc))
        except asyncio.CancelledError:
            self._runtime.subagent_store.update_instance(self._agent_id, status="killed")
            self._manager._mark_task_killed(self._task_id, "Stopped by TaskStop")
            output.stage("cancelled")
            raise
        except RunCancelled:
            # RunCancelled 是 Exception（非 BaseException），通过 asyncio.create_task
            # 重抛会触发 "Task exception was never retrieved"。直接标记 killed 并返回——
            # 清理工作已完成。
            self._runtime.subagent_store.update_instance(self._agent_id, status="killed")
            self._manager._mark_task_killed(self._task_id, "Run was cancelled")
            output.stage("cancelled")
        except Exception as exc:
            logger.exception("Background agent runner failed")
            self._runtime.subagent_store.update_instance(self._agent_id, status="failed")
            self._manager._mark_task_failed(self._task_id, str(exc))
            output.error(str(exc))
        finally:
            for task in list(self._approval_update_tasks):
                task.cancel()
            for task in list(self._approval_update_tasks):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._runtime.approval_runtime.unsubscribe(approval_subscription)
            self._runtime.approval_runtime.cancel_by_source("background_agent", self._task_id)
            reset_current_approval_source(token)
            self._manager._live_agent_tasks.pop(self._task_id, None)

    async def _run_core(self, output: SubagentOutputWriter) -> None:
        """执行 Agent 核心逻辑。

        Args:
            output: 输出写入器。
        """
        assert self._runtime.subagent_store is not None
        self._manager._mark_task_running(self._task_id)
        output.stage("runner_started")

        type_def = self._runtime.labor_market.require_builtin_type(self._subagent_type)
        record = self._runtime.subagent_store.require_instance(self._agent_id)
        launch_spec = record.launch_spec
        if self._model_override is not None:
            launch_spec = replace(
                launch_spec,
                model_override=self._model_override,
                effective_model=self._model_override,
            )

        spec = SubagentRunSpec(
            agent_id=self._agent_id,
            type_def=type_def,
            launch_spec=launch_spec,
            prompt=self._prompt,
            resumed=self._resumed,
        )
        soul, prompt = await prepare_soul(
            spec,
            self._runtime,
            self._builder,
            self._runtime.subagent_store,
            on_stage=output.stage,
        )

        async def _ui_loop_fn(wire: Wire) -> None:
            """UI 循环处理函数。"""
            wire_ui = wire.ui_side(merge=True)
            while True:
                msg = await wire_ui.receive()
                output.write_wire_message(msg)

        output.stage("run_soul_start")
        final_response, failure = await run_with_summary_continuation(
            soul,
            prompt,
            _ui_loop_fn,
            self._runtime.subagent_store.wire_path(self._agent_id),
        )
        if failure is not None:
            self._manager._mark_task_failed(self._task_id, failure.message)
            self._runtime.subagent_store.update_instance(self._agent_id, status="failed")
            output.stage(f"failed: {failure.brief}")
            return
        output.stage("run_soul_finished")

        if final_response is None:
            self._manager._mark_task_failed(
                self._task_id, "Agent completed but produced no output."
            )
            self._runtime.subagent_store.update_instance(self._agent_id, status="failed")
            output.stage("failed: empty output")
            return
        output.summary(final_response)
        self._runtime.subagent_store.update_instance(self._agent_id, status="idle")
        self._manager._mark_task_completed(self._task_id)

    def _on_approval_runtime_event(self, event: ApprovalRuntimeEvent) -> None:
        """处理审批运行时事件回调。

        Args:
            event: 审批运行时事件。
        """
        request = event.request
        if request.source.kind != "background_agent" or request.source.id != self._task_id:
            return
        task = asyncio.create_task(self._apply_approval_runtime_event(event))
        self._approval_update_tasks.add(task)
        task.add_done_callback(self._approval_update_tasks.discard)
        task.add_done_callback(self._log_approval_update_failure)

    async def _apply_approval_runtime_event(self, event: ApprovalRuntimeEvent) -> None:
        """应用审批运行时事件状态更新。

        Args:
            event: 审批运行时事件。
        """
        request = event.request
        if event.kind == "request_created":
            await asyncio.to_thread(
                self._manager._mark_task_awaiting_approval,
                self._task_id,
                request.description,
            )
        elif event.kind == "request_resolved":
            assert self._runtime.approval_runtime is not None
            pending_for_task = [
                pending
                for pending in self._runtime.approval_runtime.list_pending()
                if pending.source.kind == "background_agent" and pending.source.id == self._task_id
            ]
            if pending_for_task:
                return
            await asyncio.to_thread(
                self._manager._mark_task_running,
                self._task_id,
            )

    @staticmethod
    def _log_approval_update_failure(task: asyncio.Task[None]) -> None:
        """记录审批更新任务失败日志。

        Args:
            task: 异步任务对象。
        """
        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.opt(exception=exc).error("Failed to apply background approval state update")
