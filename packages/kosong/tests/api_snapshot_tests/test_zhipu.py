"""Snapshot tests for Zhipu chat provider."""

import json

import respx
from common import COMMON_CASES, Case, make_chat_completion_response, run_test_cases
from httpx import Response
from inline_snapshot import snapshot

from kosong.chat_provider.zhipu import Zhipu
from kosong.message import Message, TextPart, ThinkPart
from kosong.tooling import Tool

# 智谱特定的测试用例
TEST_CASES: dict[str, Case] = {
    **COMMON_CASES,
    "assistant_with_reasoning": {
        "history": [
            Message(role="user", content="What is 2+2?"),
            Message(
                role="assistant",
                content=[
                    ThinkPart(think="Let me think..."),
                    TextPart(text="The answer is 4."),
                ],
            ),
            Message(role="user", content="Thanks!"),
        ],
    },
}


async def test_zhipu_message_conversion():
    """测试消息转换，验证智谱 API 兼容格式。"""
    with respx.mock(base_url="https://open.bigmodel.cn") as mock:
        mock.post("/api/paas/v4/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response("glm-5.1"))
        )
        provider = Zhipu(model="glm-5.1", api_key="test-key", stream=False)
        results = await run_test_cases(mock, provider, TEST_CASES, ("messages", "tools"))

        assert results == snapshot(
            {
                "simple_user_message": {
                    "messages": [
                        {"role": "system", "content": "You are helpful."},
                        {"role": "user", "content": "Hello!"},
                    ],
                    "tools": [],
                },
                "multi_turn_conversation": {
                    "messages": [
                        {"role": "user", "content": "What is 2+2?"},
                        {"role": "assistant", "content": "2+2 equals 4."},
                        {"role": "user", "content": "And 3+3?"},
                    ],
                    "tools": [],
                },
                "multi_turn_with_system": {
                    "messages": [
                        {"role": "system", "content": "You are a math tutor."},
                        {"role": "user", "content": "What is 2+2?"},
                        {"role": "assistant", "content": "2+2 equals 4."},
                        {"role": "user", "content": "And 3+3?"},
                    ],
                    "tools": [],
                },
                "image_url": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "What's in this image?"},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "https://example.com/image.png",
                                        "id": None,
                                    },
                                },
                            ],
                        }
                    ],
                    "tools": [],
                },
                "tool_definition": {
                    "messages": [{"role": "user", "content": "Add 2 and 3"}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "add",
                                "description": "Add two integers.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "a": {
                                            "type": "integer",
                                            "description": "First number",
                                        },
                                        "b": {
                                            "type": "integer",
                                            "description": "Second number",
                                        },
                                    },
                                    "required": ["a", "b"],
                                },
                            },
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "multiply",
                                "description": "Multiply two integers.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "a": {"type": "integer", "description": "First number"},
                                        "b": {"type": "integer", "description": "Second number"},
                                    },
                                    "required": ["a", "b"],
                                },
                            },
                        },
                    ],
                },
                "tool_call_with_image": {
                    "messages": [
                        {"role": "user", "content": "Add 2 and 3"},
                        {
                            "role": "assistant",
                            "content": "I'll add those numbers for you.",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_abc123",
                                    "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "content": [
                                {"type": "text", "text": "5"},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "https://example.com/image.png",
                                        "id": None,
                                    },
                                },
                            ],
                            "tool_call_id": "call_abc123",
                        },
                    ],
                    "tools": [],
                },
                "tool_call": {
                    "messages": [
                        {"role": "user", "content": "Add 2 and 3"},
                        {
                            "role": "assistant",
                            "content": "I'll add those numbers for you.",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_abc123",
                                    "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
                                }
                            ],
                        },
                        {"role": "tool", "content": "5", "tool_call_id": "call_abc123"},
                    ],
                    "tools": [],
                },
                "parallel_tool_calls": {
                    "messages": [
                        {"role": "user", "content": "Calculate 2+3 and 4*5"},
                        {
                            "role": "assistant",
                            "content": "I'll calculate both.",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_add",
                                    "function": {
                                        "name": "add",
                                        "arguments": '{"a": 2, "b": 3}',
                                    },
                                },
                                {
                                    "type": "function",
                                    "id": "call_mul",
                                    "function": {
                                        "name": "multiply",
                                        "arguments": '{"a": 4, "b": 5}',
                                    },
                                },
                            ],
                        },
                        {
                            "role": "tool",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "<system-reminder>This is a system reminder"
                                    "</system-reminder>",
                                },
                                {"type": "text", "text": "5"},
                            ],
                            "tool_call_id": "call_add",
                        },
                        {
                            "role": "tool",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "<system-reminder>This is a system reminder"
                                    "</system-reminder>",
                                },
                                {"type": "text", "text": "20"},
                            ],
                            "tool_call_id": "call_mul",
                        },
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "add",
                                "description": "Add two integers.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "a": {"type": "integer", "description": "First number"},
                                        "b": {"type": "integer", "description": "Second number"},
                                    },
                                    "required": ["a", "b"],
                                },
                            },
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "multiply",
                                "description": "Multiply two integers.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "a": {"type": "integer", "description": "First number"},
                                        "b": {"type": "integer", "description": "Second number"},
                                    },
                                    "required": ["a", "b"],
                                },
                            },
                        },
                    ],
                },
                "assistant_with_reasoning": {
                    "messages": [
                        {"role": "user", "content": "What is 2+2?"},
                        {
                            "role": "assistant",
                            "content": "The answer is 4.",
                            "reasoning_content": "Let me think...",
                        },
                        {"role": "user", "content": "Thanks!"},
                    ],
                    "tools": [],
                },
            }
        )


async def test_zhipu_generation_kwargs():
    """测试生成参数配置。"""
    with respx.mock(base_url="https://open.bigmodel.cn") as mock:
        mock.post("/api/paas/v4/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = Zhipu(
            model="glm-5.1", api_key="test-key", stream=False
        ).with_generation_kwargs(temperature=0.7, max_tokens=2048)
        stream = await provider.generate("", [], [Message(role="user", content="Hi")])
        async for _ in stream:
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert (body["temperature"], body["max_tokens"]) == snapshot((0.7, 2048))


async def test_zhipu_with_thinking_enabled():
    """测试启用深度思考时的 thinking 参数。

    验证：
    - thinking 参数格式正确
    - type 为 "enabled"
    - clear_thinking 为 false（保留历史 reasoning_content）
    """
    with respx.mock(base_url="https://open.bigmodel.cn") as mock:
        mock.post("/api/paas/v4/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = Zhipu(
            model="glm-5.1", api_key="test-key", stream=False
        ).with_thinking("high")
        stream = await provider.generate("", [], [Message(role="user", content="Think")])
        async for _ in stream:
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert body["thinking"] == snapshot(
            {"type": "enabled", "clear_thinking": False}
        )


async def test_zhipu_with_thinking_off():
    """测试禁用深度思考时的 thinking 参数。"""
    with respx.mock(base_url="https://open.bigmodel.cn") as mock:
        mock.post("/api/paas/v4/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = Zhipu(
            model="glm-5.1", api_key="test-key", stream=False
        ).with_thinking("off")
        stream = await provider.generate("", [], [Message(role="user", content="Think")])
        async for _ in stream:
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert body["thinking"] == snapshot(
            {"type": "disabled", "clear_thinking": False}
        )


async def test_zhipu_thinking_effort_property():
    """测试 thinking_effort 属性。"""
    provider = Zhipu(model="glm-5.1", api_key="test-key")
    assert provider.thinking_effort is None

    provider_enabled = provider.with_thinking("high")
    assert provider_enabled.thinking_effort == "medium"  # 智谱不支持级别，默认返回 medium

    provider_disabled = provider.with_thinking("off")
    assert provider_disabled.thinking_effort == "off"


async def test_zhipu_reasoning_content_in_response():
    """测试响应中的 reasoning_content 转换为 ThinkPart。"""
    with respx.mock(base_url="https://open.bigmodel.cn") as mock:
        # 模拟包含 reasoning_content 的响应
        mock.post("/api/paas/v4/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1234567890,
                    "model": "glm-5.1",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "The answer is 4.",
                                "reasoning_content": "Let me think about this...",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        )
        provider = Zhipu(model="glm-5.1", api_key="test-key", stream=False)
        stream = await provider.generate("", [], [Message(role="user", content="What is 2+2?")])

        parts = []
        async for part in stream:
            parts.append(part)

        # 验证第一个部分是 ThinkPart
        assert isinstance(parts[0], ThinkPart)
        assert parts[0].think == "Let me think about this..."
        assert parts[0].encrypted is None  # 智谱 API 不返回加密字段

        # 验证第二个部分是 TextPart
        assert isinstance(parts[1], TextPart)
        assert parts[1].text == "The answer is 4."


async def test_zhipu_custom_base_url():
    """测试自定义 base_url。"""
    with respx.mock(base_url="https://custom.zhipu.api") as mock:
        mock.post("/v4/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = Zhipu(
            model="glm-5.1",
            api_key="test-key",
            base_url="https://custom.zhipu.api/v4/",
            stream=False,
        )
        stream = await provider.generate("", [], [Message(role="user", content="Hi")])
        async for _ in stream:
            pass
        # 验证请求发送到了自定义 URL
        assert mock.calls.last is not None


async def test_zhipu_api_key_from_env():
    """测试从环境变量读取 API key。"""
    import os

    original = os.environ.get("ZHIPU_API_KEY")
    try:
        os.environ["ZHIPU_API_KEY"] = "env-test-key"
        provider = Zhipu(model="glm-5.1")
        assert provider._api_key == "env-test-key"
    finally:
        if original is not None:
            os.environ["ZHIPU_API_KEY"] = original
        else:
            os.environ.pop("ZHIPU_API_KEY", None)


async def test_zhipu_missing_api_key():
    """测试缺少 API key 时抛出错误。"""
    import os

    original = os.environ.get("ZHIPU_API_KEY")
    try:
        os.environ.pop("ZHIPU_API_KEY", None)
        try:
            Zhipu(model="glm-5.1")
            assert False, "Expected ChatProviderError"
        except Exception as e:
            assert "ZHIPU_API_KEY" in str(e)
    finally:
        if original is not None:
            os.environ["ZHIPU_API_KEY"] = original
