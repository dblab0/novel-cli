"""Snapshot tests for OpenRouter chat provider."""

import json

import respx
from common import COMMON_CASES, Case, make_chat_completion_response, run_test_cases
from httpx import Response
from inline_snapshot import snapshot

from kosong.contrib.chat_provider.openrouter import OpenRouter
from kosong.message import Message, TextPart, ThinkPart

TEST_CASES: dict[str, Case] = {
    **COMMON_CASES,
}


async def test_openrouter_message_conversion():
    """测试消息转换，验证 OpenRouter API 兼容格式。"""
    with respx.mock(base_url="https://openrouter.ai") as mock:
        mock.post("/api/v1/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response("google/gemma-4-31b-it:free"))
        )
        provider = OpenRouter(model="google/gemma-4-31b-it:free", api_key="test-key", stream=False)
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
            }
        )


async def test_openrouter_with_thinking_injects_extra_body():
    """测试启用 reasoning 时注入 extra_body 参数。"""
    with respx.mock(base_url="https://openrouter.ai") as mock:
        mock.post("/api/v1/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = OpenRouter(
            model="google/gemma-4-31b-it:free", api_key="test-key", stream=False
        ).with_thinking("high")
        stream = await provider.generate("", [], [Message(role="user", content="Think")])
        async for _ in stream:
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert body["reasoning"] == snapshot({"enabled": True})


async def test_openrouter_with_thinking_off_no_extra_body():
    """测试禁用 reasoning 时不注入 extra_body。"""
    with respx.mock(base_url="https://openrouter.ai") as mock:
        mock.post("/api/v1/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = OpenRouter(
            model="google/gemma-4-31b-it:free", api_key="test-key", stream=False
        ).with_thinking("off")
        stream = await provider.generate("", [], [Message(role="user", content="Think")])
        async for _ in stream:
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert "reasoning" not in body


async def test_openrouter_reasoning_details_in_response():
    """测试响应中的 reasoning_details 转换为 ThinkPart。"""
    with respx.mock(base_url="https://openrouter.ai") as mock:
        mock.post("/api/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1234567890,
                    "model": "google/gemma-4-31b-it:free",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "The answer is 4.",
                                "reasoning_details": "Let me think about this...",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        )
        provider = OpenRouter(
            model="google/gemma-4-31b-it:free", api_key="test-key", stream=False
        )
        stream = await provider.generate("", [], [Message(role="user", content="What is 2+2?")])

        parts = []
        async for part in stream:
            parts.append(part)

        assert isinstance(parts[0], ThinkPart)
        assert parts[0].think == "Let me think about this..."
        assert isinstance(parts[1], TextPart)
        assert parts[1].text == "The answer is 4."


async def test_openrouter_reasoning_details_list_format():
    """测试 reasoning_details 为列表格式时的解析。"""
    with respx.mock(base_url="https://openrouter.ai") as mock:
        mock.post("/api/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1234567890,
                    "model": "google/gemma-4-31b-it:free",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "42",
                                "reasoning_details": [
                                    {"type": "summary", "summary": "Step 1: analyze"},
                                    {"type": "summary", "summary": "Step 2: compute"},
                                ],
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        )
        provider = OpenRouter(
            model="google/gemma-4-31b-it:free", api_key="test-key", stream=False
        )
        stream = await provider.generate("", [], [Message(role="user", content="What is 6*7?")])

        parts = []
        async for part in stream:
            parts.append(part)

        assert isinstance(parts[0], ThinkPart)
        assert "Step 1: analyze" in parts[0].think
        assert "Step 2: compute" in parts[0].think


async def test_openrouter_custom_base_url():
    """测试自定义 base_url。"""
    with respx.mock(base_url="https://custom.proxy") as mock:
        mock.post("/v1/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = OpenRouter(
            model="google/gemma-4-31b-it:free",
            api_key="test-key",
            base_url="https://custom.proxy/v1",
            stream=False,
        )
        stream = await provider.generate("", [], [Message(role="user", content="Hi")])
        async for _ in stream:
            pass
        assert mock.calls.last is not None


async def test_openrouter_generation_kwargs():
    """测试生成参数配置。"""
    with respx.mock(base_url="https://openrouter.ai") as mock:
        mock.post("/api/v1/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = OpenRouter(
            model="google/gemma-4-31b-it:free", api_key="test-key", stream=False
        ).with_generation_kwargs(temperature=0.7, max_tokens=2048)
        stream = await provider.generate("", [], [Message(role="user", content="Hi")])
        async for _ in stream:
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert (body["temperature"], body["max_tokens"]) == snapshot((0.7, 2048))


async def test_openrouter_default_base_url():
    """测试默认 base_url 为 OpenRouter API 地址。"""
    provider = OpenRouter(model="test-model", api_key="test-key")
    assert str(provider.client.base_url) == "https://openrouter.ai/api/v1/"
