from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import cast

import aiohttp

from windcode.domain.errors import ErrorCategory
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
from windcode.providers.errors import ProviderError, error_from_http_status, map_provider_error

ChunkFactory = Callable[[ModelRequest], AsyncIterator[Mapping[str, object]]]


def build_chat_messages(
    messages: tuple[Message, ...], system_prompt: str
) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    if system_prompt:
        converted.append({"role": "system", "content": system_prompt})
    for message in messages:
        text_parts: list[str] = []
        tool_calls: list[dict[str, object]] = []
        tool_results: list[ToolResultBlock] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ReasoningBlock):
                text_parts.append(block.summary)
            elif isinstance(block, AttachmentBlock):
                text_parts.append(f"[attachment: {block.description or block.reference}]")
            elif isinstance(block, ToolCallBlock):
                tool_calls.append(
                    {
                        "id": block.call_id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.arguments, separators=(",", ":")),
                        },
                    }
                )
            else:
                tool_results.append(block)
        if text_parts or tool_calls:
            item: dict[str, object] = {
                "role": message.role.value,
                "content": "\n".join(text_parts) or None,
            }
            if tool_calls:
                item["tool_calls"] = tool_calls
            converted.append(item)
        for result in tool_results:
            converted.append(
                {"role": "tool", "tool_call_id": result.call_id, "content": result.content}
            )
    return converted


def build_chat_tools(request: ModelRequest) -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in request.tools
    ]


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    raw = cast(Mapping[object, object], value)
    return {str(key): child for key, child in raw.items()}


class OpenAICompatibleTransport(BaseTransport):
    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        session: aiohttp.ClientSession | None = None,
        chunk_factory: ChunkFactory | None = None,
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._chunk_factory = chunk_factory or self._http_chunks
        if session is not None:
            self._owns_session = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))
            self.add_close_callback(self._session.close)
        return self._session

    async def _http_chunks(self, request: ModelRequest) -> AsyncIterator[Mapping[str, object]]:
        session = await self._get_session()
        url = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )
        body: dict[str, object] = {
            "model": request.model,
            "messages": build_chat_messages(request.messages, request.system_prompt),
            "tools": build_chat_tools(request),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.max_output_tokens is not None:
            body["max_tokens"] = request.max_output_tokens
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "text/event-stream"}
        async with session.post(url, json=body, headers=headers) as response:
            if response.status >= 400:
                message = await response.text()
                raise error_from_http_status(response.status, message)
            async for raw_line in response.content:
                line = raw_line.decode("utf-8", errors="strict").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    return
                try:
                    decoded = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderError(f"invalid SSE JSON: {exc}", ErrorCategory.NETWORK) from exc
                if not isinstance(decoded, Mapping):
                    raise ProviderError("SSE payload must be an object", ErrorCategory.NETWORK)
                raw = cast(Mapping[object, object], decoded)
                yield {str(key): value for key, value in raw.items()}

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.ensure_open()
        usage = Usage()
        calls: dict[int, tuple[str, str]] = {}
        completed = False
        try:
            async for chunk in self._chunk_factory(request):
                error = get_value(chunk, "error")
                if error is not None:
                    message = as_string(get_value(error, "message"), "provider returned an error")
                    raise ProviderError(message, ErrorCategory.INVALID_REQUEST)
                raw_usage = get_value(chunk, "usage")
                if raw_usage is not None:
                    prompt_details = get_value(raw_usage, "prompt_tokens_details")
                    usage = Usage(
                        input_tokens=as_int(get_value(raw_usage, "prompt_tokens")),
                        output_tokens=as_int(get_value(raw_usage, "completion_tokens")),
                        cache_read_tokens=as_int(get_value(prompt_details, "cached_tokens")),
                    )
                    yield ModelUsage(usage)
                raw_choices = get_value(chunk, "choices", [])
                if not isinstance(raw_choices, list) or not raw_choices:
                    continue
                choices = cast(list[object], raw_choices)
                choice = choices[0]
                delta = get_value(choice, "delta", {})
                text = as_string(get_value(delta, "content"))
                if text:
                    yield TextDelta(text)
                reasoning = as_string(
                    get_value(delta, "reasoning_content", get_value(delta, "reasoning", ""))
                )
                if reasoning:
                    yield ReasoningDelta(reasoning)
                raw_tool_calls_value = get_value(delta, "tool_calls", [])
                if isinstance(raw_tool_calls_value, list):
                    raw_tool_calls = cast(list[object], raw_tool_calls_value)
                    for raw_call in raw_tool_calls:
                        call = _mapping(raw_call)
                        index = as_int(get_value(call, "index"))
                        function = get_value(call, "function", {})
                        previous_id, previous_name = calls.get(index, ("", ""))
                        call_id = as_string(get_value(call, "id"), previous_id)
                        name = as_string(get_value(function, "name"), previous_name)
                        calls[index] = (call_id, name)
                        yield ToolCallDelta(
                            call_id,
                            name,
                            as_string(get_value(function, "arguments")),
                        )
                finish_reason = as_string(get_value(choice, "finish_reason"))
                if finish_reason:
                    reason = {
                        "tool_calls": StopReason.TOOL_USE,
                        "length": StopReason.MAX_TOKENS,
                        "content_filter": StopReason.CONTENT_FILTER,
                        "stop": StopReason.STOP,
                    }.get(finish_reason, StopReason.STOP)
                    yield ModelCompleted(reason, usage)
                    completed = True
            if not completed:
                raise ProviderError(
                    "chat completion stream ended without a finish reason",
                    ErrorCategory.NETWORK,
                )
        except BaseException as exc:
            raise map_provider_error(exc) from exc
