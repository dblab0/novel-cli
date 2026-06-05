"""测试工具模块。

提供用于测试和调试的简单工具类。
"""

import asyncio
from typing import override

from kosong.tooling import CallableTool2, ToolOk, ToolReturnValue
from pydantic import BaseModel


class PlusParams(BaseModel):
    """加法工具参数。

    Attributes:
        a: 第一个操作数。
        b: 第二个操作数。
    """

    a: float
    b: float


class Plus(CallableTool2[PlusParams]):
    """加法工具。

    计算两个数值的和。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "plus"
    description: str = "Add two numbers"
    params: type[PlusParams] = PlusParams

    @override
    async def __call__(self, params: PlusParams) -> ToolReturnValue:
        """执行加法运算。

        Args:
            params: 包含两个操作数的参数。

        Returns:
            两数之和的字符串表示。
        """
        return ToolOk(output=str(params.a + params.b))


class CompareParams(BaseModel):
    """比较工具参数。

    Attributes:
        a: 第一个操作数。
        b: 第二个操作数。
    """

    a: float
    b: float


class Compare(CallableTool2[CompareParams]):
    """数值比较工具。

    比较两个数值的大小关系。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "compare"
    description: str = "Compare two numbers"
    params: type[CompareParams] = CompareParams

    @override
    async def __call__(self, params: CompareParams) -> ToolReturnValue:
        """执行数值比较。

        Args:
            params: 包含两个操作数的参数。

        Returns:
            "greater"、"less" 或 "equal" 表示比较结果。
        """
        if params.a > params.b:
            return ToolOk(output="greater")
        elif params.a < params.b:
            return ToolOk(output="less")
        else:
            return ToolOk(output="equal")


class PanicParams(BaseModel):
    """恐慌工具参数。

    Attributes:
        message: 恐慌消息内容。
    """

    message: str


class Panic(CallableTool2[PanicParams]):
    """恐慌测试工具。

    用于测试工具调用失败的情况，会抛出异常。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "panic"
    description: str = "Raise an exception to cause the tool call to fail."
    params: type[PanicParams] = PanicParams

    @override
    async def __call__(self, params: PanicParams) -> ToolReturnValue:
        """执行恐慌操作，延迟后抛出异常。

        Args:
            params: 包含消息内容的参数。

        Returns:
            此方法不会正常返回，总是抛出异常。

        Raises:
            Exception: 总是抛出异常。
        """
        await asyncio.sleep(2)
        raise Exception(f"panicked with a message with {len(params.message)} characters")
