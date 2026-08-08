import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from windcode.domain.errors import ErrorCategory, WindcodeError
from windcode.domain.messages import Message, ReasoningBlock, Role, TextBlock
from windcode.domain.models import ModelEvent, ModelRequest, TextDelta
from windcode.providers import ModelTarget
from windcode.runtime.retry import portable_messages, stream_with_retry


@dataclass
class ScriptedTransport:
    name: str
    failures: int
    received: list[ModelRequest]

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.received.append(request)
        if self.failures > 0:
            self.failures -= 1
            raise WindcodeError("temporary", ErrorCategory.SERVER)
        yield TextDelta(self.name)

    async def aclose(self) -> None:
        pass


class HangingTransport:
    name = "hanging"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        await asyncio.Event().wait()
        yield TextDelta("unreachable")

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_retries_twice_then_explicitly_falls_back() -> None:
    primary = ScriptedTransport("primary", 3, [])
    backup = ScriptedTransport("backup", 0, [])
    retries: list[tuple[str, int]] = []
    fallbacks: list[tuple[str, str]] = []

    async def on_retry(target: ModelTarget, attempt: int, _error: WindcodeError) -> None:
        retries.append((target.provider, attempt))

    async def on_fallback(source: ModelTarget, target: ModelTarget, _error: WindcodeError) -> None:
        fallbacks.append((source.provider, target.provider))

    async def no_sleep(_delay: float) -> None:
        pass

    events = [
        event
        async for _target, event in stream_with_retry(
            (
                ModelTarget("primary", "primary-model", primary),
                ModelTarget("backup", "backup-model", backup),
            ),
            ModelRequest(model="ignored", messages=(), system_prompt="system"),
            on_retry=on_retry,
            on_fallback=on_fallback,
            sleep=no_sleep,
        )
    ]

    assert retries == [("primary", 1), ("primary", 2)]
    assert fallbacks == [("primary", "backup")]
    assert events == [TextDelta("backup")]
    assert backup.received[0].model == "backup-model"


def test_portable_messages_remove_provider_opaque_state() -> None:
    original = (
        Message(
            Role.ASSISTANT,
            (ReasoningBlock("summary", {"signature": "secret"}), TextBlock("text")),
            provider_metadata={"provider": "private"},
        ),
    )
    portable = portable_messages(original)
    reasoning = portable[0].content[0]
    assert isinstance(reasoning, ReasoningBlock)
    assert reasoning.summary == "summary"
    assert reasoning.opaque == {}
    assert portable[0].provider_metadata == {}


@pytest.mark.asyncio
async def test_idle_model_stream_times_out_as_retryable_network_error() -> None:
    async def ignore_retry(_target: ModelTarget, _attempt: int, _error: WindcodeError) -> None:
        pass

    async def ignore_fallback(
        _source: ModelTarget, _target: ModelTarget, _error: WindcodeError
    ) -> None:
        pass

    with pytest.raises(WindcodeError) as raised:
        _ = [
            event
            async for _target, event in stream_with_retry(
                (ModelTarget("hanging", "model", HangingTransport()),),
                ModelRequest(model="model", messages=(), system_prompt="system"),
                on_retry=ignore_retry,
                on_fallback=ignore_fallback,
                max_retries=0,
                idle_timeout_seconds=0.01,
            )
        ]

    assert raised.value.category is ErrorCategory.NETWORK
    assert raised.value.retryable
    assert "produced no data" in str(raised.value)
