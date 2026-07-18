from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import cast

from anthropic import AsyncAnthropic, DefaultAioHttpClient
from anthropic.types import MessageParam, ToolUnionParam

from windcode.domain.messages import (
    AttachmentBlock,
    Message,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    ModelUsage,
    ReasoningDelta,
    StopReason,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from windcode.providers._utils import as_int, as_string, get_value
from windcode.providers.base import BaseTransport
from windcode.providers.errors import map_provider_error

RawStreamFactory = Callable[[ModelRequest], AsyncIterator[object]]


def _content_block(block: object) -> dict[str, object]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ReasoningBlock):
        return {"type": "text", "text": block.summary}
    if isinstance(block, ToolCallBlock):
        return {
            "type": "tool_use",
            "id": block.call_id,
            "name": block.name,
            "input": block.arguments,
        }
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.call_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    if isinstance(block, AttachmentBlock):
        return {
            "type": "text",
            "text": f"[attachment: {block.description or block.reference}]",
        }
    raise TypeError(f"unsupported content block: {type(block).__name__}")


def build_anthropic_messages(messages: tuple[Message, ...]) -> list[MessageParam]:
    converted: list[MessageParam] = []
    for message in messages:
        if message.role is Role.SYSTEM:
            continue
        role = "assistant" if message.role is Role.ASSISTANT else "user"
        converted.append(
            cast(
                MessageParam,
                {"role": role, "content": [_content_block(block) for block in message.content]},
            )
        )
    return converted


def build_anthropic_tools(request: ModelRequest) -> list[ToolUnionParam]:
    return [
        cast(
            ToolUnionParam,
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            },
        )
        for tool in request.tools
    ]


def _stop_reason(value: object) -> StopReason:
    raw = as_string(value)
    return {
        "end_turn": StopReason.STOP,
        "stop_sequence": StopReason.STOP,
        "tool_use": StopReason.TOOL_USE,
        "max_tokens": StopReason.MAX_TOKENS,
        "refusal": StopReason.CONTENT_FILTER,
    }.get(raw, StopReason.STOP)


class AnthropicTransport(BaseTransport):
    name = "anthropic_messages"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        stream_factory: RawStreamFactory | None = None,
    ) -> None:
        super().__init__()
        self._client: AsyncAnthropic | None = None
        if stream_factory is None:
            http_client = DefaultAioHttpClient()
            self._client = AsyncAnthropic(
                api_key=api_key,
                base_url=base_url,
                max_retries=0,
                http_client=http_client,
            )
            self.add_close_callback(self._client.close)
            self._stream_factory = self._sdk_stream
        else:
            self._stream_factory = stream_factory

    async def _sdk_stream(self, request: ModelRequest) -> AsyncIterator[object]:
        if self._client is None:
            raise RuntimeError("Anthropic client is not configured")
        stream = await self._client.messages.create(
            model=request.model,
            messages=build_anthropic_messages(request.messages),
            system=request.system_prompt,
            tools=build_anthropic_tools(request),
            max_tokens=request.max_output_tokens or 4096,
            stream=True,
        )
        async for event in stream:
            yield event

    def _convert_event(self, event: object, usage: Usage) -> tuple[list[ModelEvent], Usage, bool]:
        event_type = as_string(get_value(event, "type"))
        emitted: list[ModelEvent] = []
        completed = False
        if event_type == "message_start":
            raw_usage = get_value(get_value(event, "message"), "usage")
            usage = Usage(
                input_tokens=as_int(get_value(raw_usage, "input_tokens")),
                output_tokens=usage.output_tokens,
                cache_read_tokens=as_int(get_value(raw_usage, "cache_read_input_tokens")),
                cache_write_tokens=as_int(get_value(raw_usage, "cache_creation_input_tokens")),
            )
            emitted.append(ModelUsage(usage))
        elif event_type == "content_block_start":
            block = get_value(event, "content_block")
            if as_string(get_value(block, "type")) == "tool_use":
                initial = get_value(block, "input", {})
                arguments = "" if initial == {} else json.dumps(initial, separators=(",", ":"))
                emitted.append(
                    ToolCallDelta(
                        call_id=as_string(get_value(block, "id")),
                        name=as_string(get_value(block, "name")),
                        arguments_delta=arguments,
                    )
                )
        elif event_type == "content_block_delta":
            delta = get_value(event, "delta")
            delta_type = as_string(get_value(delta, "type"))
            if delta_type == "text_delta":
                emitted.append(TextDelta(as_string(get_value(delta, "text"))))
            elif delta_type in {"thinking_delta", "signature_delta"}:
                summary = as_string(get_value(delta, "thinking", get_value(delta, "signature", "")))
                emitted.append(ReasoningDelta(summary))
            elif delta_type == "input_json_delta":
                emitted.append(
                    ToolCallDelta(
                        call_id="",
                        name="",
                        arguments_delta=as_string(get_value(delta, "partial_json")),
                    )
                )
        elif event_type == "message_delta":
            raw_usage = get_value(event, "usage")
            usage = Usage(
                input_tokens=usage.input_tokens,
                output_tokens=as_int(get_value(raw_usage, "output_tokens"), usage.output_tokens),
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
            emitted.append(ModelUsage(usage))
            emitted.append(
                ModelCompleted(
                    reason=_stop_reason(get_value(get_value(event, "delta"), "stop_reason")),
                    usage=usage,
                )
            )
            completed = True
        return emitted, usage, completed

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.ensure_open()
        usage = Usage()
        completed = False
        try:
            async for raw_event in self._stream_factory(request):
                events, usage, just_completed = self._convert_event(raw_event, usage)
                completed = completed or just_completed
                for event in events:
                    yield event
            if not completed:
                yield ModelCompleted(StopReason.STOP, usage)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            raise map_provider_error(exc) from exc
