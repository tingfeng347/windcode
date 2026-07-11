from collections.abc import AsyncIterator, Mapping

import pytest

from windcode.domain.errors import ErrorCategory, WindcodeError
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
from windcode.providers.openai_compat import OpenAICompatibleTransport, build_chat_messages


async def fake_chunks(_request: ModelRequest) -> AsyncIterator[Mapping[str, object]]:
    yield {"choices": [{"delta": {"reasoning_content": "checking"}}]}
    yield {"choices": [{"delta": {"content": "hello"}}]}
    yield {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call",
                            "function": {"name": "read_file", "arguments": '{"path":'},
                        }
                    ]
                }
            }
        ]
    }
    yield {
        "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"a"}'}}]}}]
    }
    yield {"usage": {"prompt_tokens": 7, "completion_tokens": 4}, "choices": []}
    yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}


@pytest.mark.asyncio
async def test_normalizes_chat_completion_chunks() -> None:
    transport = OpenAICompatibleTransport(
        api_key="test", base_url="http://localhost/v1", chunk_factory=fake_chunks
    )
    events = [
        event
        async for event in transport.stream(
            ModelRequest(model="test", messages=(), system_prompt="system")
        )
    ]

    assert events[:4] == [
        ReasoningDelta("checking"),
        TextDelta("hello"),
        ToolCallDelta("call", "read_file", '{"path":'),
        ToolCallDelta("call", "read_file", '"a"}'),
    ]
    usage_event = events[-2]
    assert isinstance(usage_event, ModelUsage)
    assert events[-1] == ModelCompleted(StopReason.TOOL_USE, usage_event.usage)


@pytest.mark.asyncio
async def test_reports_truncated_stream_as_retryable_network_error() -> None:
    async def truncated(_request: ModelRequest) -> AsyncIterator[Mapping[str, object]]:
        yield {"choices": [{"delta": {"content": "partial"}}]}

    transport = OpenAICompatibleTransport(
        api_key="test", base_url="http://localhost/v1", chunk_factory=truncated
    )
    with pytest.raises(WindcodeError) as raised:
        _ = [
            event
            async for event in transport.stream(
                ModelRequest(model="test", messages=(), system_prompt="")
            )
        ]
    assert raised.value.category is ErrorCategory.NETWORK
    assert raised.value.retryable


def test_converts_chat_tool_messages() -> None:
    messages = (
        Message(Role.ASSISTANT, (TextBlock("calling"), ToolCallBlock("call", "read", {}))),
        Message(Role.TOOL, (ToolResultBlock("call", "read", "contents"),)),
    )

    converted = build_chat_messages(messages, "system")

    assert [item["role"] for item in converted] == ["system", "assistant", "tool"]
    assert converted[1]["tool_calls"]
    assert converted[2]["tool_call_id"] == "call"
