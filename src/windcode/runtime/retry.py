from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import cast

from windcode.domain.errors import WindcodeError
from windcode.domain.messages import Message, ReasoningBlock
from windcode.domain.models import ModelEvent, ModelRequest
from windcode.providers import ModelTarget
from windcode.providers.errors import map_provider_error

RetryCallback = Callable[[ModelTarget, int, WindcodeError], Awaitable[None]]
FallbackCallback = Callable[[ModelTarget, ModelTarget, WindcodeError], Awaitable[None]]
SleepCallback = Callable[[float], Awaitable[None]]


async def _stream_with_idle_timeout(
    stream: AsyncIterator[ModelEvent],
    timeout_seconds: float,
) -> AsyncIterator[ModelEvent]:
    iterator = stream.__aiter__()
    while True:
        try:
            async with asyncio.timeout(timeout_seconds):
                event = await anext(iterator)
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            raise TimeoutError(
                f"model stream produced no data for {timeout_seconds:g} seconds"
            ) from exc
        yield event


def portable_messages(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    portable: list[Message] = []
    for message in messages:
        content = tuple(
            replace(block, opaque={}) if isinstance(block, ReasoningBlock) else block
            for block in message.content
        )
        portable.append(replace(message, content=content, provider_metadata={}))
    return tuple(portable)


async def stream_with_retry(
    chain: tuple[ModelTarget, ...],
    request: ModelRequest,
    *,
    on_retry: RetryCallback,
    on_fallback: FallbackCallback,
    max_retries: int = 2,
    idle_timeout_seconds: float = 60.0,
    sleep: SleepCallback = asyncio.sleep,
) -> AsyncIterator[tuple[ModelTarget, ModelEvent]]:
    if not chain:
        raise ValueError("model chain cannot be empty")
    current_request: ModelRequest = request
    for target_index, target in enumerate(chain):
        attempts = 0
        while True:
            provider_request = replace(current_request, model=target.model)
            try:
                async for event in _stream_with_idle_timeout(
                    target.transport.stream(provider_request), idle_timeout_seconds
                ):
                    yield target, event
                return
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                error = map_provider_error(exc)
                if error.retryable and attempts < max_retries:
                    attempts += 1
                    await on_retry(target, attempts, error)
                    delay = min(4.0, 0.25 * (2 ** (attempts - 1)))
                    await sleep(delay * random.uniform(0.8, 1.2))
                    continue
                has_fallback = target_index + 1 < len(chain)
                if error.fallback_allowed and has_fallback:
                    fallback = chain[target_index + 1]
                    await on_fallback(target, fallback, error)
                    current_request = cast(
                        ModelRequest,
                        replace(
                            current_request,
                            messages=portable_messages(current_request.messages),
                        ),
                    )
                    break
                raise error from exc
