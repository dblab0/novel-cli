"""向量嵌入辅助模块，提供通过 OpenAI-compatible API 获取文本嵌入向量的功能。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from pydantic import SecretStr

if TYPE_CHECKING:
    pass


async def get_embedding(
    text: str,
    api_url: str,
    api_key: SecretStr,
    model: str = "Qwen/Qwen3-Embedding-4B",
    instruct: str = "给定一个实体搜索查询，检索能够回答该查询的相关实体描述",
) -> list[float]:
    """通过 OpenAI-compatible API 获取给定文本的嵌入向量。

    查询文本会被包装成问题格式以获得更好的匹配效果。

    Args:
        text: 待嵌入的文本内容。
        api_url: 嵌入 API 的 URL 地址。
        api_key: API 密钥，使用 SecretStr 包装。
        model: 嵌入模型名称，默认为 "Qwen/Qwen3-Embedding-4B"。
        instruct: 指令文本，用于指导嵌入模型的检索行为。

    Returns:
        嵌入向量，为浮点数列表。

    Raises:
        httpx.HTTPStatusError: API 请求失败时抛出。
    """
    query_text = f"{text}是什么"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{api_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            json={
                "model": model,
                "input": [f"Instruct: {instruct}\nQuery: {query_text}"],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]