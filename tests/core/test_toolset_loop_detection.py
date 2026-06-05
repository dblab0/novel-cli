"""NovelToolset 循环检测集成测试。

测试 NovelToolset.handle() 中的循环检测触发和误触发场景。
"""

import asyncio

import pytest
from kosong.tooling import CallableTool2, ToolOk
from kosong.utils.typing import JsonType
from pydantic import BaseModel

from novel_cli.soul.toolset import NovelToolset, ToolCallLoopDetected
from novel_cli.wire.types import ToolCall


class _DummyParams(BaseModel):
    command: str = "echo hi"


class _DummyTool(CallableTool2[_DummyParams]):
    """用于测试的 mock 工具，始终返回 ok。"""

    def __init__(self) -> None:
        super().__init__(
            name="shell",
            description="dummy shell tool",
            params=_DummyParams,
        )

    async def __call__(self, params: _DummyParams) -> JsonType:
        return ToolOk(output=f"executed: {params.command}")


@pytest.fixture
def toolset() -> NovelToolset:
    """创建带循环检测和 mock 工具的工具集实例。"""
    ts = NovelToolset(loop_threshold=5)
    ts.add(_DummyTool())
    return ts


def _make_tool_call(name: str, arguments: str, call_id: str = "tc-1") -> ToolCall:
    """构造 ToolCall 测试对象。"""
    return ToolCall(
        id=call_id,
        type="function",
        function={"name": name, "arguments": arguments},
    )


async def test_handle_raises_on_loop(toolset: NovelToolset) -> None:
    """handle() 连续重复调用时，第 N 次抛出 ToolCallLoopDetected。"""
    tc = _make_tool_call("shell", '{"command": "echo stuck"}')
    # 前 4 次正常执行
    for _ in range(4):
        result = toolset.handle(tc)
        if isinstance(result, asyncio.Task):
            await result
    # 第 5 次 handle → _call() 内 LoopDetector.check() 抛异常
    result = toolset.handle(tc)
    with pytest.raises(ToolCallLoopDetected):
        if isinstance(result, asyncio.Task):
            await result


async def test_handle_different_calls_no_trigger(toolset: NovelToolset) -> None:
    """不同 tool_call 不会误触发循环检测。"""
    tc_a = _make_tool_call("shell", '{"command": "echo a"}', call_id="tc-a")
    tc_b = _make_tool_call("shell", '{"command": "echo b"}', call_id="tc-b")
    for _ in range(10):
        for tc in [tc_a, tc_b]:
            result = toolset.handle(tc)
            if isinstance(result, asyncio.Task):
                await result
