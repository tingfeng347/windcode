from collections.abc import AsyncIterator

import pytest

from windcode.context import CHECKPOINT_SECTIONS, compact_context
from windcode.domain.messages import Message, Role, TextBlock
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta


class FakeTransport:
    name = "fake"

    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        prompt_block = request.messages[-1].content[0]
        assert isinstance(prompt_block, TextBlock)
        assert "结构化检查点" in prompt_block.text
        if self.error is not None:
            raise self.error
        yield TextDelta(self.text)
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


def valid_checkpoint() -> str:
    return "\n".join(f"## {section}\n内容" for section in CHECKPOINT_SECTIONS)


@pytest.mark.asyncio
async def test_builds_checkpoint_and_preserves_recent_messages() -> None:
    messages = tuple(Message(Role.USER, (TextBlock(f"message {index}"),)) for index in range(20))
    result = await compact_context(
        messages,
        FakeTransport(valid_checkpoint()),
        model="model",
        system_prompt="system",
        preserve_recent_turns=2,
    )

    assert result.compacted
    assert result.checkpoint == valid_checkpoint()
    assert len(result.messages) == 5
    assert result.messages[-1] == messages[-1]


@pytest.mark.asyncio
async def test_failure_keeps_original_view() -> None:
    messages = (Message(Role.USER, (TextBlock("original"),)),)
    result = await compact_context(
        messages,
        FakeTransport(error=RuntimeError("offline")),
        model="model",
        system_prompt="system",
    )
    assert result.messages is messages
    assert not result.compacted
    assert "offline" in (result.error or "")


@pytest.mark.asyncio
async def test_invalid_summary_keeps_original_view() -> None:
    messages = (Message(Role.USER, (TextBlock("original"),)),)
    result = await compact_context(
        messages, FakeTransport("incomplete"), model="model", system_prompt="system"
    )
    assert result.messages is messages
    assert not result.compacted
