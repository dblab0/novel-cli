"""智谱 AI 深度思考示例。

演示如何启用 GLM 模型的深度思考功能。
"""

import asyncio

from dotenv import load_dotenv

from kosong import generate
from kosong.chat_provider.zhipu import Zhipu
from kosong.message import Message

load_dotenv()


async def main():
    # 创建启用深度思考的 provider（从 ZHIPU_API_KEY 环境变量读取 api_key）
    chat = Zhipu(model="glm-4.7-flash").with_thinking("high")

    history: list[Message] = [
        Message(role="user", content="请解释一下相对论的基本原理"),
    ]
    result = await generate(chat, "你是一个物理学家", [], history)

    # 打印思考过程和回答
    from kosong.message import TextPart, ThinkPart

    for part in result.message.content:
        if isinstance(part, ThinkPart) and part.think:
            print(f"[思考] {part.think}")
        elif isinstance(part, TextPart) and part.text:
            print(f"[回答] {part.text}")


if __name__ == "__main__":
    asyncio.run(main())
