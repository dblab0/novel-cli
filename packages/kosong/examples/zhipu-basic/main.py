"""智谱 AI 基础对话示例。

演示如何使用 Zhipu provider 进行基础对话和多轮对话。
"""

import asyncio

from dotenv import load_dotenv

from kosong import generate
from kosong.chat_provider.zhipu import Zhipu
from kosong.message import Message

load_dotenv()


async def main():
    # 创建智谱 provider（从 ZHIPU_API_KEY 环境变量读取 api_key）
    chat = Zhipu(model="glm-4.7-flash")

    # 基础对话
    history: list[Message] = [
        Message(role="user", content="你好，请介绍一下你自己"),
    ]
    result = await generate(chat, "你是一个有用的AI助手", [], history)
    print(result.message.extract_text())
    print(f"Token 使用量: {result.usage}")

    # 多轮对话
    history.append(result.message)
    history.append(Message(role="user", content="你能做什么？"))
    result = await generate(chat, "你是一个有用的AI助手", [], history)
    print(result.message.extract_text())


if __name__ == "__main__":
    asyncio.run(main())
