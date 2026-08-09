from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from windcode.domain.errors import WindcodeError
from windcode.domain.events import (
    ModelFallback,
    ModelRetrying,
    ModelStarted,
    ReasoningStatus,
    TextDeltaEvent,
    UsageUpdated,
)
from windcode.domain.messages import Message, Role, TextBlock, ToolCallBlock
from windcode.domain.models import (
    ModelRequest,
    ModelUsage,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from windcode.providers import ModelTarget
from windcode.runtime.control import RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.retry import stream_with_retry
from windcode.runtime.scheduler import ScheduledCall

EventFields = Callable[[], dict[str, Any]]


@dataclass(slots=True)
class ModelSession:
    model_chain: tuple[ModelTarget, ...]
    system_prompt: str
    max_output_tokens: int | None = None
    stream_idle_timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class ModelTurnOutcome:
    text: str
    total_usage: Usage
    assistant_message: Message
    scheduled_calls: tuple[ScheduledCall, ...]
    raw_arguments: dict[str, dict[str, Any]]


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_read_tokens=left.cache_read_tokens + right.cache_read_tokens,
        cache_write_tokens=left.cache_write_tokens + right.cache_write_tokens,
    )


class ModelTurnRunner:
    """Collect one streamed model response into an executable turn outcome."""

    def __init__(
        self,
        model: ModelSession,
        control: RunControl,
        event_bus: EventBus,
        event_fields: EventFields,
    ) -> None:
        self._model = model
        self._control = control
        self._event_bus = event_bus
        self._event_fields = event_fields

    async def _on_retry(
        self,
        target: ModelTarget,
        attempt: int,
        error: WindcodeError,
    ) -> None:
        await self._event_bus.publish(
            ModelRetrying(
                **self._event_fields(),
                model=target.model,
                attempt=attempt,
                reason=str(error),
            )
        )

    async def _on_fallback(
        self,
        source: ModelTarget,
        target: ModelTarget,
        error: WindcodeError,
    ) -> None:
        await self._event_bus.publish(
            ModelFallback(
                **self._event_fields(),
                from_model=source.model,
                to_model=target.model,
                reason=str(error),
            ),
            durable=True,
        )
        await self._event_bus.publish(ModelStarted(**self._event_fields(), model=target.model))

    async def execute(self, request: ModelRequest, previous_usage: Usage) -> ModelTurnOutcome:
        text_parts: list[str] = []
        call_order: list[str] = []
        calls: dict[str, dict[str, str]] = {}
        last_call_id = ""
        step_usage = Usage()

        async for _target, event in stream_with_retry(
            self._model.model_chain,
            request,
            on_retry=self._on_retry,
            on_fallback=self._on_fallback,
            idle_timeout_seconds=self._model.stream_idle_timeout_seconds,
        ):
            self._control.check()
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
                await self._event_bus.publish(
                    TextDeltaEvent(**self._event_fields(), text=event.text)
                )
            elif isinstance(event, ReasoningDelta):
                await self._event_bus.publish(
                    ReasoningStatus(**self._event_fields(), status=event.summary)
                )
            elif isinstance(event, ToolCallDelta):
                call_id = event.call_id or last_call_id or uuid4().hex
                if call_id not in calls:
                    calls[call_id] = {"name": event.name, "arguments": ""}
                    call_order.append(call_id)
                calls[call_id]["name"] = event.name or calls[call_id]["name"]
                calls[call_id]["arguments"] += event.arguments_delta
                last_call_id = call_id
            elif isinstance(event, ModelUsage):
                step_usage = event.usage
                await self._event_bus.publish(
                    UsageUpdated(
                        **self._event_fields(), usage=_add_usage(previous_usage, step_usage)
                    )
                )
            else:
                step_usage = event.usage

        text = "".join(text_parts)
        assistant_content: list[TextBlock | ToolCallBlock] = []
        if text:
            assistant_content.append(TextBlock(text))

        scheduled: list[ScheduledCall] = []
        raw_arguments: dict[str, dict[str, Any]] = {}
        for call_id in call_order:
            state = calls[call_id]
            try:
                decoded = json.loads(state["arguments"] or "{}")
                if not isinstance(decoded, Mapping):
                    raise ValueError("tool arguments must be an object")
                mapping = cast(Mapping[object, object], decoded)
                arguments = {str(key): value for key, value in mapping.items()}
            except (json.JSONDecodeError, ValueError) as exc:
                arguments = {"_invalid_json": state["arguments"], "_error": str(exc)}
            raw_arguments[call_id] = arguments
            assistant_content.append(ToolCallBlock(call_id, state["name"], arguments))
            scheduled.append(ScheduledCall(call_id, state["name"], arguments))

        return ModelTurnOutcome(
            text=text,
            total_usage=_add_usage(previous_usage, step_usage),
            assistant_message=Message(Role.ASSISTANT, tuple(assistant_content)),
            scheduled_calls=tuple(scheduled),
            raw_arguments=raw_arguments,
        )
