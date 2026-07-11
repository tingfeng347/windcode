from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from windcode.domain.messages import Message


class StopReason(StrEnum):
    STOP = "stop"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    CONTENT_FILTER = "content_filter"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    model: str
    messages: tuple[Message, ...]
    system_prompt: str
    tools: tuple[ToolSchema, ...] = ()
    max_output_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    summary: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    call_id: str
    name: str
    arguments_delta: str


@dataclass(frozen=True, slots=True)
class ModelUsage:
    usage: Usage


@dataclass(frozen=True, slots=True)
class ModelCompleted:
    reason: StopReason
    usage: Usage = field(default_factory=Usage)
    opaque: dict[str, Any] = field(default_factory=dict[str, Any], repr=False)


ModelEvent = TextDelta | ReasoningDelta | ToolCallDelta | ModelUsage | ModelCompleted
