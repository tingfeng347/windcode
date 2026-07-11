from collections.abc import AsyncIterator
from typing import cast

import pytest

from windcode.domain.messages import Message, Role, TextBlock, ToolCallBlock, ToolResultBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelRequest,
    ModelUsage,
    ReasoningDelta,
    StopReason,
    TextDelta,
    ToolCallDelta,
    ToolSchema,
)
from windcode.providers.anthropic import AnthropicTransport, build_anthropic_messages


async def fake_stream(_request: ModelRequest) -> AsyncIterator[object]:
    yield {"type": "message_start", "message": {"usage": {"input_tokens": 12}}}
    yield {
        "type": "content_block_delta",
        "delta": {"type": "thinking_delta", "thinking": "checking"},
    }
    yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}}
    yield {
        "type": "content_block_start",
        "content_block": {"type": "tool_use", "id": "call", "name": "read_file", "input": {}},
    }
    yield {
        "type": "content_block_delta",
        "delta": {"type": "input_json_delta", "partial_json": '{"path":"README.md"}'},
    }
    yield {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use"},
        "usage": {"output_tokens": 8},
    }


@pytest.mark.asyncio
async def test_normalizes_anthropic_stream() -> None:
    request = ModelRequest(
        model="claude-test",
        messages=(Message(Role.USER, (TextBlock("hi"),)),),
        system_prompt="system",
        tools=(ToolSchema("read_file", "Read", {"type": "object"}),),
    )
    events = [
        event async for event in AnthropicTransport(stream_factory=fake_stream).stream(request)
    ]

    assert isinstance(events[0], ModelUsage)
    assert ReasoningDelta("checking") in events
    assert TextDelta("hello") in events
    assert ToolCallDelta("call", "read_file", "") in events
    assert ToolCallDelta("", "", '{"path":"README.md"}') in events
    usage_event = events[-2]
    assert isinstance(usage_event, ModelUsage)
    assert events[-1] == ModelCompleted(StopReason.TOOL_USE, usage_event.usage)


def test_converts_tool_messages() -> None:
    messages = (
        Message(Role.ASSISTANT, (ToolCallBlock("call", "read_file", {"path": "a"}),)),
        Message(Role.TOOL, (ToolResultBlock("call", "read_file", "contents"),)),
    )

    converted = build_anthropic_messages(messages)

    assert converted[0]["role"] == "assistant"
    assert converted[1]["role"] == "user"
    content = cast(list[dict[str, object]], converted[1]["content"])
    assert content[0]["type"] == "tool_result"
