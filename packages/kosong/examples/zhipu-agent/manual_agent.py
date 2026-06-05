"""智谱 AI 手动 Agent 循环示例。

使用 kosong.generate（底层 API）手动实现 Agent 循环，
不依赖 kosong.step，完整打印思考过程、文本回复、工具调用参数和工具返回结果。
"""

import asyncio
import json

from dotenv import load_dotenv
from pydantic import BaseModel, Field

import kosong
from kosong.chat_provider.zhipu import Zhipu
from kosong.message import Message, TextPart, ThinkPart, ToolCall
from kosong.tooling import CallableTool2, ToolOk, ToolReturnValue
from kosong.tooling.simple import SimpleToolset

load_dotenv()


# ── 工具定义 ──────────────────────────────────────────────


class CalculateParams(BaseModel):
    expression: str = Field(description="数学表达式")


class CalculateTool(CallableTool2[CalculateParams]):
    name = "calculate"
    description = "执行数学计算"
    params = CalculateParams

    async def __call__(self, params: CalculateParams) -> ToolReturnValue:
        try:
            result_value = eval(params.expression)  # noqa: S307
            return ToolOk(
                output=str(result_value),
                brief=f"{params.expression} = {result_value}",
            )
        except Exception as e:
            return ToolOk(output=f"计算错误: {e}", brief="计算错误")


# ── 打印辅助函数 ──────────────────────────────────────────


def print_message_detail(msg: Message) -> None:
    """打印消息中的思考过程和文本内容。"""
    for part in msg.content:
        if isinstance(part, ThinkPart) and part.think:
            print(f"  [思考] {part.think}")
        elif isinstance(part, TextPart) and part.text:
            print(f"  [回复] {part.text}")


def print_tool_calls(tool_calls: list[ToolCall]) -> None:
    """打印工具调用信息。"""
    for tc in tool_calls:
        print(f"  [工具调用] name={tc.function.name}")
        if tc.function.arguments:
            try:
                args = json.loads(tc.function.arguments)
                for key, value in args.items():
                    print(f"    参数: {key} = {value}")
            except json.JSONDecodeError:
                print(f"    原始参数: {tc.function.arguments}")


# ── 主逻辑 ────────────────────────────────────────────────


async def manual_agent():
    """使用 kosong.generate 手动实现 Agent 循环。"""
    chat = Zhipu(model="glm-4.7-flash",base_url="http://192.168.0.199:4000/v1").with_thinking("high")

    # 创建工具并获取工具定义列表
    calc_tool = CalculateTool()
    toolset = SimpleToolset()
    toolset += calc_tool
    tools = toolset.tools

    # 构建工具名 -> 工具实例的映射，用于手动调用
    tool_map: dict[str, CalculateTool] = {t.name: calc_tool for t in tools}

    system_prompt = "你是一个数学助手，使用 calculate 工具帮助用户计算。用中文回答。"
    history: list[Message] = [
        Message(role="user", content="请计算 (23 + 17) * 2 的结果，然后再把结果除以 8"),
    ]

    max_iterations = 5
    for iteration in range(max_iterations):
        print(f"\n{'='*50}")
        print(f"第 {iteration + 1} 轮")
        print(f"{'='*50}")

        # 使用 generate（底层 API）而非 step
        result = await kosong.generate(
            chat_provider=chat,
            system_prompt=system_prompt,
            tools=tools,
            history=history,
        )

        # 将助手消息加入历史
        history.append(result.message)

        # 打印 token 用量
        if result.usage:
            print(f"  [Token] input={result.usage.input_other}, "
                  f"output={result.usage.output}")

        # 打印思考过程和文本回复
        print_message_detail(result.message)

        # 检查是否有工具调用
        if not result.message.tool_calls:
            print("\n  [完成] 模型未调用工具，Agent 循环结束。")
            break

        # 打印工具调用参数
        print_tool_calls(result.message.tool_calls)

        # 手动执行每个工具调用
        for tc in result.message.tool_calls:
            # 解析参数
            arguments = json.loads(tc.function.arguments or "{}")

            # 手动调用工具
            tool_instance = tool_map.get(tc.function.name)
            if tool_instance is None:
                tool_output = f"未知工具: {tc.function.name}"
                print(f"  [工具结果] {tool_output}")
            else:
                return_value = await tool_instance.call(arguments)
                tool_output = (
                    return_value.output
                    if isinstance(return_value.output, str)
                    else str(return_value.output)
                )
                print(f"  [工具结果] {return_value.brief or tool_output}")

            # 将工具结果加入历史
            history.append(
                Message(
                    role="tool",
                    tool_call_id=tc.id,
                    content=tool_output,
                )
            )


if __name__ == "__main__":
    asyncio.run(manual_agent())
