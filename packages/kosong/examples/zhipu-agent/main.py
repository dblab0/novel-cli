"""智谱 AI Agent 循环示例。

演示一个简单的 Agent，可以多次调用工具直到完成任务。
使用 kosong.step 的方式实现。
"""

import asyncio
import sys

from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, Field

import kosong
from kosong.chat_provider.zhipu import Zhipu
from kosong.message import Message, ThinkPart
from kosong.tooling import CallableTool2, ToolOk, ToolReturnValue
from kosong.tooling.simple import SimpleToolset

# 在 kosong import 之后开启 trace 日志（kosong.__init__ 会 disable logger）
logger.remove()
logger.add(sys.stderr, level="TRACE")
logger.enable("kosong")

load_dotenv()


class CalculateParams(BaseModel):
    expression: str = Field(description="数学表达式")


class CalculateTool(CallableTool2[CalculateParams]):
    name = "calculate"
    description = "执行数学计算"
    params = CalculateParams

    async def __call__(self, params: CalculateParams) -> ToolReturnValue:
        try:
            result_value = eval(params.expression)
            return ToolOk(output=str(result_value), brief=f"{params.expression} = {result_value}")
        except Exception as e:
            return ToolOk(output=f"计算错误: {e}", brief=f"计算错误")


async def simple_agent():
    """一个简单的 Agent，使用 kosong.step 实现工具调用循环"""
    # 从 ZHIPU_API_KEY 环境变量读取 api_key
    chat = Zhipu(model="glm-4.7-flash",base_url="http://192.168.0.199:4000/v1").with_thinking("high")

    toolset = SimpleToolset()
    toolset += CalculateTool()

    system_prompt = "你是一个数学助手，使用 calculate 工具帮助用户计算。"
    history: list[Message] = [
        Message(role="user", content="请计算 (23 + 17) * 2 的结果，然后再把结果除以 8"),
    ]

    max_iterations = 5
    for iteration in range(max_iterations):
        print(f"\n--- 第 {iteration + 1} 步 ---")

        result = await kosong.step(
            chat_provider=chat,
            system_prompt=system_prompt,
            toolset=toolset,
            history=history,
        )

        # 将生成的消息添加到历史
        history.append(result.message)

        # 打印思考过程
        for part in result.message.content:
            if isinstance(part, ThinkPart) and part.think:
                print(f"[思考] {part.think}")

        # 获取工具调用结果
        tool_results = await result.tool_results()

        if not tool_results:
            # 没有工具调用，输出最终答案
            print(f"[回答] {result.message.extract_text()}")
            break

        # 打印工具结果并添加到历史
        for tool_result in tool_results:
            print(f"[工具] {tool_result.return_value.brief}")
            history.append(
                Message(
                    role="tool",
                    tool_call_id=tool_result.tool_call_id,
                    content=tool_result.return_value.output,
                )
            )


if __name__ == "__main__":
    asyncio.run(simple_agent())
