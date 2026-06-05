"""NovelToolset book 参数注入与 Schema 隐藏测试。

测试 NovelToolset 对小说搜索工具的 book 参数注入机制，
以及 tools 属性过滤小说搜索工具的 book 参数 Schema。
"""

from __future__ import annotations

import json

from kosong.tooling import CallableTool2, ToolOk
from pydantic import BaseModel, Field

from novel_cli.soul.toolset import NovelToolset
from novel_cli.wire.types import ToolCall, ToolResult


class MockSearchParams(BaseModel):
    """模拟搜索工具参数类。

    Attributes:
        query: 查询文本。
        book: 书名过滤器。
    """

    query: str = Field(description="搜索查询")
    book: str | None = Field(default=None, description="书名过滤器")


class MockSearchEntity(CallableTool2[MockSearchParams]):
    """模拟 SearchEntity 工具。

    用于测试 book 参数注入机制，不依赖真实数据库连接。
    """

    name: str = "SearchEntity"
    description: str = "模拟的 SearchEntity 工具"
    params: type[MockSearchParams] = MockSearchParams

    def __init__(self) -> None:
        """初始化 mock 工具，记录实际调用参数。"""
        super().__init__()
        self.last_call_params: dict | None = None

    async def __call__(self, params: MockSearchParams):
        """记录调用参数并返回成功结果。

        Args:
            params: 搜索参数。

        Returns:
            成功的 ToolOk 结果。
        """
        self.last_call_params = params.model_dump()
        return ToolOk(output=f"搜索结果: query={params.query}, book={params.book}")


async def test_book_param_injection() -> None:
    """测试 book 参数自动注入。

    当 get_current_book 返回值时，book 参数应被自动注入到 SearchEntity 调用中。
    """
    import asyncio

    toolset = NovelToolset(get_current_book=lambda: "凡人修仙传")
    mock_tool = MockSearchEntity()
    toolset.add(mock_tool)

    tool_call = ToolCall(
        id="test-id",
        function=ToolCall.FunctionBody(
            name="SearchEntity",
            arguments=json.dumps({"query": "韩立"}),
        ),
    )

    # 获取 handle 结果并等待完成
    handle_result = toolset.handle(tool_call)
    assert isinstance(handle_result, asyncio.Task)

    # 等待任务完成并验证结果
    tool_result = await handle_result
    assert isinstance(tool_result, ToolResult)

    # 验证 book 参数被注入到 mock_tool 的调用参数中
    assert mock_tool.last_call_params is not None
    assert mock_tool.last_call_params["book"] == "凡人修仙传"
    assert mock_tool.last_call_params["query"] == "韩立"


async def test_book_param_not_overwrite() -> None:
    """测试 setdefault 不覆盖已有值。

    当 agent 已传入 book 参数时，不应被 get_current_book 的值覆盖。
    """
    import asyncio

    toolset = NovelToolset(get_current_book=lambda: "凡人修仙传")
    mock_tool = MockSearchEntity()
    toolset.add(mock_tool)

    tool_call = ToolCall(
        id="test-id",
        function=ToolCall.FunctionBody(
            name="SearchEntity",
            arguments=json.dumps({"query": "韩立", "book": "凡人修仙之仙界篇"}),
        ),
    )

    # 执行 handle 并等待结果
    handle_result = toolset.handle(tool_call)
    assert isinstance(handle_result, asyncio.Task)
    tool_result = await handle_result
    assert isinstance(tool_result, ToolResult)

    # 验证已传入的 book 值保持不变（未被覆盖）
    assert mock_tool.last_call_params is not None
    assert mock_tool.last_call_params["book"] == "凡人修仙之仙界篇"
    assert mock_tool.last_call_params["query"] == "韩立"


def test_book_schema_hidden() -> None:
    """测试 tools 属性过滤小说搜索工具的 book 参数。

    验证小说搜索工具的 book 参数在对 LLM 展示的 Schema 中被移除，
    避免 LLM 直接设置该参数而绕过 session 的 current_book 机制。
    """
    toolset = NovelToolset(get_current_book=lambda: "凡人修仙传")
    mock_tool = MockSearchEntity()
    toolset.add(mock_tool)

    tools = toolset.tools
    search_tool = next((t for t in tools if t.name == "SearchEntity"), None)

    assert search_tool is not None
    # book 参数不应在 properties 中
    assert "book" not in search_tool.parameters["properties"]
    # book 参数不应在 required 中
    assert "book" not in search_tool.parameters.get("required", [])
    # query 参数应保留（它是必需的）
    assert "query" in search_tool.parameters["properties"]


def test_book_schema_hidden_only_for_search_tools() -> None:
    """测试只有小说搜索工具的 book 参数被隐藏。

    验证其他工具不受 Schema 过滤影响。
    """
    from kosong.tooling import ToolOk
    from pydantic import BaseModel

    class OtherParams(BaseModel):
        """其他工具参数。"""
        value: str = ""
        book: str | None = None

    class OtherTool(CallableTool2[OtherParams]):
        """其他工具，非小说搜索工具。"""
        name: str = "OtherTool"
        description: str = "其他工具"
        params: type[OtherParams] = OtherParams

        async def __call__(self, _params: OtherParams):
            return ToolOk(output="ok")

    toolset = NovelToolset(get_current_book=lambda: "凡人修仙传")
    toolset.add(MockSearchEntity())
    toolset.add(OtherTool())

    tools = toolset.tools
    search_tool = next((t for t in tools if t.name == "SearchEntity"), None)
    other_tool = next((t for t in tools if t.name == "OtherTool"), None)

    # 小说搜索工具的 book 参数被隐藏
    assert search_tool is not None
    assert "book" not in search_tool.parameters["properties"]

    # OtherTool 的 book 参数应保留
    assert other_tool is not None
    assert "book" in other_tool.parameters["properties"]


async def test_book_injection_with_none_current_book() -> None:
    """测试 get_current_book 返回 None 时不注入 book 参数。

    验证当 current_book 为 None 时，不会注入空值或 None 到参数中。
    """
    import asyncio

    toolset = NovelToolset(get_current_book=lambda: None)
    mock_tool = MockSearchEntity()
    toolset.add(mock_tool)

    tool_call = ToolCall(
        id="test-id",
        function=ToolCall.FunctionBody(
            name="SearchEntity",
            arguments=json.dumps({"query": "韩立"}),
        ),
    )

    # 执行 handle 并等待结果
    handle_result = toolset.handle(tool_call)
    assert isinstance(handle_result, asyncio.Task)
    tool_result = await handle_result
    assert isinstance(tool_result, ToolResult)

    # 验证 book 参数未被注入（保持原始的 None 值）
    assert mock_tool.last_call_params is not None
    assert mock_tool.last_call_params["book"] is None
    assert mock_tool.last_call_params["query"] == "韩立"


def test_toolset_without_get_current_book() -> None:
    """测试未配置 get_current_book 的 toolset 行为。

    验证没有 get_current_book 回调时，tools 属性仍正确过滤小说搜索工具的 book 参数。
    """
    toolset = NovelToolset()  # 无 get_current_book
    mock_tool = MockSearchEntity()
    toolset.add(mock_tool)

    tools = toolset.tools
    search_tool = next((t for t in tools if t.name == "SearchEntity"), None)

    # 即使没有 get_current_book，Schema 过滤仍生效
    assert search_tool is not None
    assert "book" not in search_tool.parameters["properties"]