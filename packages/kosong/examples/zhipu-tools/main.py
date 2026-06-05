"""智谱 AI 工具调用示例。

演示如何使用智谱 GLM 模型进行 Function Call。
"""

import asyncio
import json

from dotenv import load_dotenv

from kosong import generate
from kosong.chat_provider.zhipu import Zhipu
from kosong.message import Message
from kosong.tooling import Tool

load_dotenv()


# 定义工具
def get_weather(city: str) -> str:
    """获取城市天气（模拟）"""
    return f"{city}今天天气晴朗，气温25°C"


async def main():
    # 从 ZHIPU_API_KEY 环境变量读取 api_key
    chat = Zhipu(model="glm-4.7-flash")

    tools = [
        Tool(
            name="get_weather",
            description="获取指定城市的天气信息",
            parameters={
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称",
                    },
                },
                "required": ["city"],
            },
        ),
    ]

    history: list[Message] = [
        Message(role="user", content="北京今天天气怎么样？"),
    ]

    # 第一轮：模型可能调用工具
    result = await generate(chat, "你是一个天气助手", tools, history)
    history.append(result.message)

    # 处理工具调用
    if result.message.tool_calls:
        for tool_call in result.message.tool_calls:
            if tool_call.function.name == "get_weather":
                args = json.loads(tool_call.function.arguments or "{}")
                weather = get_weather(args["city"])
                # 添加工具结果到历史
                history.append(
                    Message(
                        role="tool",
                        tool_call_id=tool_call.id,
                        content=weather,
                    )
                )

        # 第二轮：模型根据工具结果回答
        result = await generate(chat, "你是一个天气助手", tools, history)
        print(result.message.extract_text())


if __name__ == "__main__":
    asyncio.run(main())
