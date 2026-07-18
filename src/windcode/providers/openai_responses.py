from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import cast

from openai import AsyncOpenAI, DefaultAioHttpClient
from openai.types.responses import ResponseInputParam, ToolParam

from windcode.domain.messages import (
    AttachmentBlock,
    Message,
    ReasoningBlock,
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


def _message_text(message: Message) -> str:
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ReasoningBlock):
            parts.append(block.summary)
        elif isinstance(block, AttachmentBlock):
            parts.append(f"[attachment: {block.description or block.reference}]")
    return "\n".join(parts)


def build_responses_input(messages: tuple[Message, ...]) -> ResponseInputParam:
    items: list[object] = []
    for message in messages:
        text = _message_text(message)
        if text:
            items.append({"type": "message", "role": message.role.value, "content": text})
        for block in message.content:
            if isinstance(block, ToolCallBlock):
                items.append(
                    {
                        "type": "function_call",
                        "call_id": block.call_id,
                        "name": block.name,
                        "arguments": json.dumps(block.arguments, separators=(",", ":")),
                    }
                )
            elif isinstance(block, ToolResultBlock):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.call_id,
                        "output": block.content,
                    }
                )
    return cast(ResponseInputParam, items)


def build_responses_tools(request: ModelRequest) -> list[ToolParam]:
    return [
        cast(
            ToolParam,
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": True,
            },
        )
        for tool in request.tools
    ]


class OpenAIResponsesTransport(BaseTransport):
    name = "openai_responses"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        stream_factory: RawStreamFactory | None = None,
    ) -> None:
        super().__init__()
        self._client: AsyncOpenAI | None = None
        if stream_factory is None:
            http_client = DefaultAioHttpClient()
            self._client = AsyncOpenAI(
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
            raise RuntimeError("OpenAI client is not configured")
        stream = await self._client.responses.create(
            model=request.model,
            instructions=request.system_prompt,
            input=build_responses_input(request.messages),
            tools=build_responses_tools(request),
            max_output_tokens=request.max_output_tokens,
            stream=True,
            store=False,
        )
        async for event in stream:
            yield event

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.ensure_open()
        usage = Usage()
        calls: dict[str, tuple[str, str]] = {}
        completed = False
        try:
            async for event in self._stream_factory(request):
                event_type = as_string(get_value(event, "type"))
                if event_type == "response.output_text.delta":
                    yield TextDelta(as_string(get_value(event, "delta")))
                elif event_type in {
                    "response.reasoning_summary_text.delta",
                    "response.reasoning_text.delta",
                }:
                    yield ReasoningDelta(as_string(get_value(event, "delta")))
                elif event_type == "response.output_item.added":
                    item = get_value(event, "item")
                    if as_string(get_value(item, "type")) == "function_call":
                        item_id = as_string(
                            get_value(item, "id", get_value(event, "output_index", ""))
                        )
                        call_id = as_string(get_value(item, "call_id", item_id))
                        name = as_string(get_value(item, "name"))
                        calls[item_id] = (call_id, name)
                        arguments = as_string(get_value(item, "arguments"))
                        yield ToolCallDelta(call_id, name, arguments)
                elif event_type == "response.function_call_arguments.delta":
                    item_id = as_string(
                        get_value(event, "item_id", get_value(event, "output_index", ""))
                    )
                    call_id, name = calls.get(item_id, (item_id, ""))
                    yield ToolCallDelta(call_id, name, as_string(get_value(event, "delta")))
                elif event_type in {"response.completed", "response.incomplete"}:
                    response = get_value(event, "response")
                    raw_usage = get_value(response, "usage")
                    input_details = get_value(raw_usage, "input_tokens_details")
                    usage = Usage(
                        input_tokens=as_int(get_value(raw_usage, "input_tokens")),
                        output_tokens=as_int(get_value(raw_usage, "output_tokens")),
                        cache_read_tokens=as_int(get_value(input_details, "cached_tokens")),
                    )
                    yield ModelUsage(usage)
                    if event_type == "response.incomplete":
                        reason = StopReason.MAX_TOKENS
                    elif calls:
                        reason = StopReason.TOOL_USE
                    else:
                        reason = StopReason.STOP
                    yield ModelCompleted(reason, usage)
                    completed = True
                elif event_type == "response.failed":
                    response = get_value(event, "response")
                    error = get_value(response, "error", get_value(event, "error"))
                    message = as_string(get_value(error, "message"), "OpenAI response failed")
                    raise RuntimeError(message)
            if not completed:
                yield ModelCompleted(StopReason.STOP, usage)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            raise map_provider_error(exc) from exc
