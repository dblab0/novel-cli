"""subagent 循环阻断测试。

验证 subagent 内部触发 ToolCallLoopDetected 时，
通过 RunCancelled 传播，最终实例状态标记为 "killed"。

参照 test_agent_tool.py 中 test_agent_tool_marks_instance_killed_when_run_cancelled 模式。
"""

from __future__ import annotations

from kosong.tooling.empty import EmptyToolset

from novel_cli.soul import RunCancelled
from novel_cli.soul.agent import Agent as SoulAgent
from novel_cli.soul.toolset import ToolCallLoopDetected
from novel_cli.subagents import AgentTypeDefinition, ToolPolicy


async def test_subagent_loop_detection_marks_killed(agent_tool, runtime, monkeypatch):
    """subagent 循环检测 → RunCancelled → 实例状态 "killed"。"""
    runtime.labor_market.add_builtin_type(
        AgentTypeDefinition(
            name="coder",
            description="Good at general software engineering tasks.",
            agent_file=runtime.subagent_store.root / "coder.yaml",
            tool_policy=ToolPolicy(mode="inherit"),
        )
    )

    async def fake_load_agent(agent_file, runtime, *, mcp_configs, start_mcp_loading=True):
        return SoulAgent(
            name=agent_file.stem,
            system_prompt="Subagent system prompt",
            toolset=EmptyToolset(),
            runtime=runtime,
        )

    async def fake_run_soul(
        soul, user_input, ui_loop_fn, cancel_event, wire_file=None, runtime=None
    ):
        # 模拟 ToolCallLoopDetected 在 _agent_loop 中被转为 RunCancelled
        raise RunCancelled("工具 shell 连续 5 次以相同参数调用，已阻断")

    monkeypatch.setattr("novel_cli.subagents.builder.load_agent", fake_load_agent)
    monkeypatch.setattr("novel_cli.subagents.runner.run_soul", fake_run_soul)

    # RunCancelled 从 run_soul 传播上来，被 AgentTool 捕获
    result = await agent_tool(
        agent_tool.params(
            description="loop detection test",
            prompt="investigate bug",
        )
    )

    assert result.is_error
    # 验证实例状态为 "killed"
    records = [
        r
        for r in runtime.subagent_store.list_instances()
        if r.description == "loop detection test"
    ]
    assert len(records) == 1
    assert records[0].status == "killed"
