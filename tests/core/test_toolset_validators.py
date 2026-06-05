"""tool_validators 声明式工具调用顺序验证测试。"""

from __future__ import annotations

import json

from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.agentspec import ToolValidator
from novel_cli.soul.toolset import NovelToolset
from novel_cli.wire.types import ToolCall, ToolResult


# --- Mock 工具 ---


class MockGraphParams(BaseModel):
    """模拟 SearchGraph 参数。"""

    entity_id: str = Field(description="实体 ID")
    rel_type: str | None = Field(default=None, description="关系类型")


class MockSearchGraph(CallableTool2[MockGraphParams]):
    """模拟 SearchGraph 工具。"""

    name: str = "SearchGraph"
    description: str = "mock"
    params: type[MockGraphParams] = MockGraphParams

    async def __call__(self, params: MockGraphParams) -> ToolReturnValue:
        if params.rel_type:
            return ToolOk(output=f"详情: {params.entity_id} / {params.rel_type}")
        return ToolOk(output=f"概览: {params.entity_id}")


class MockOtherParams(BaseModel):
    """模拟其他工具参数。"""

    value: str = ""


class MockOtherTool(CallableTool2[MockOtherParams]):
    """模拟非目标工具。"""

    name: str = "OtherTool"
    description: str = "mock"
    params: type[MockOtherParams] = MockOtherParams

    async def __call__(self, params: MockOtherParams) -> ToolReturnValue:
        return ToolOk(output=f"ok: {params.value}")


# --- 辅助函数 ---


def _make_toolset(validators: list[ToolValidator] | None = None) -> NovelToolset:
    """构造带验证器的 toolset。"""
    ts = NovelToolset(tool_validators=validators)
    ts.add(MockSearchGraph())
    ts.add(MockOtherTool())
    return ts


async def _call(ts: NovelToolset, tool: str, args: dict) -> ToolResult:
    """构造 ToolCall → handle → 等待完成 → 返回 ToolResult。"""
    tool_call = ToolCall(
        id="tc-1",
        function=ToolCall.FunctionBody(
            name=tool,
            arguments=json.dumps(args),
        ),
    )
    result = ts.handle(tool_call)
    # handle 返回 asyncio.Task（异步工具）
    import asyncio

    assert isinstance(result, asyncio.Task)
    return await result


# --- 测试用例 ---


async def test_overview_then_detail_passes():
    """先概览后详情 → 正常通过。"""
    validators = [ToolValidator(tool="SearchGraph", require_overview_before_detail=True)]
    ts = _make_toolset(validators)

    r1 = await _call(ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0"})
    assert isinstance(r1.return_value, ToolOk)

    r2 = await _call(
        ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0", "rel_type": "亲密"}
    )
    assert isinstance(r2.return_value, ToolOk)


async def test_detail_without_overview_blocked():
    """未概览直接详情 → 被拦截。"""
    validators = [ToolValidator(tool="SearchGraph", require_overview_before_detail=True)]
    ts = _make_toolset(validators)

    r = await _call(
        ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0", "rel_type": "亲密"}
    )
    assert isinstance(r.return_value, ToolError)
    assert "概览" in r.return_value.message


async def test_multi_entity_independent():
    """多实体独立追踪：A 概览后 A 详情通过，B 详情被拦截。"""
    validators = [ToolValidator(tool="SearchGraph", require_overview_before_detail=True)]
    ts = _make_toolset(validators)

    # A 概览
    ra = await _call(ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0"})
    assert isinstance(ra.return_value, ToolOk)

    # A 详情 → 通过
    ra2 = await _call(
        ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0", "rel_type": "亲密"}
    )
    assert isinstance(ra2.return_value, ToolOk)

    # B 详情（未概览）→ 拦截
    rb = await _call(
        ts, "SearchGraph", {"entity_id": "西游记_人物_猪八戒_0", "rel_type": "对立"}
    )
    assert isinstance(rb.return_value, ToolError)


async def test_no_validators_passes_through():
    """无验证器 → 一切正常，无拦截。"""
    ts = _make_toolset(validators=None)

    r = await _call(
        ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0", "rel_type": "亲密"}
    )
    assert isinstance(r.return_value, ToolOk)


async def test_overview_allows_multiple_detail_calls():
    """概览一次后可多次调用详情。"""
    validators = [ToolValidator(tool="SearchGraph", require_overview_before_detail=True)]
    ts = _make_toolset(validators)

    await _call(ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0"})

    r1 = await _call(
        ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0", "rel_type": "亲密"}
    )
    assert isinstance(r1.return_value, ToolOk)

    r2 = await _call(
        ts, "SearchGraph", {"entity_id": "西游记_人物_孙悟空_0", "rel_type": "对立"}
    )
    assert isinstance(r2.return_value, ToolOk)


async def test_non_target_tool_unaffected():
    """非目标工具不受验证器影响。"""
    validators = [ToolValidator(tool="SearchGraph", require_overview_before_detail=True)]
    ts = _make_toolset(validators)

    r = await _call(ts, "OtherTool", {"value": "test"})
    assert isinstance(r.return_value, ToolOk)
