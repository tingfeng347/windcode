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
)
from windcode.providers.openai_responses import OpenAIResponsesTransport, build_responses_input


async def fake_stream(_request: ModelRequest) -> AsyncIterator[object]:
    yield {"type": "response.reasoning_summary_text.delta", "delta": "checking"}
    yield {"type": "response.output_text.delta", "delta": "hello"}
    yield {
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "item", "call_id": "call", "name": "read_file"},
    }
    yield {
        "type": "response.function_call_arguments.delta",
        "item_id": "item",
        "delta": '{"path":"README.md"}',
    }
    yield {
        "type": "response.completed",
        "response": {
            "usage": {
                "input_tokens": 12,
                "output_tokens": 8,
                "input_tokens_details": {"cached_tokens": 3},
            }
        },
    }


@pytest.mark.asyncio
async def test_normalizes_responses_stream() -> None:
    request = ModelRequest(model="gpt-test", messages=(), system_prompt="system")
    events = [
        event
        async for event in OpenAIResponsesTransport(stream_factory=fake_stream).stream(request)
    ]

    assert events[:4] == [
        ReasoningDelta("checking"),
        TextDelta("hello"),
        ToolCallDelta("call", "read_file", ""),
        ToolCallDelta("call", "read_file", '{"path":"README.md"}'),
    ]
    usage_event = events[-2]
    assert isinstance(usage_event, ModelUsage)
    assert usage_event.usage.cache_read_tokens == 3
    assert events[-1] == ModelCompleted(StopReason.TOOL_USE, usage_event.usage)


def test_converts_function_calls_and_outputs() -> None:
    messages = (
        Message(Role.ASSISTANT, (TextBlock("calling"), ToolCallBlock("call", "read", {}))),
        Message(Role.TOOL, (ToolResultBlock("call", "read", "contents"),)),
    )

    converted = cast(list[dict[str, object]], build_responses_input(messages))

    assert [item["type"] for item in converted] == [
        "message",
        "function_call",
        "function_call_output",
    ]
