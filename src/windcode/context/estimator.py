from __future__ import annotations

import math
from dataclasses import dataclass

from windcode.domain.messages import (
    Message,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from windcode.domain.models import ModelRequest, Usage


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def estimate_message_tokens(message: Message) -> int:
    tokens = 4
    for block in message.content:
        if isinstance(block, TextBlock):
            tokens += estimate_text_tokens(block.text)
        elif isinstance(block, ReasoningBlock):
            tokens += estimate_text_tokens(block.summary)
        elif isinstance(block, ToolCallBlock):
            tokens += estimate_text_tokens(block.name) + estimate_text_tokens(str(block.arguments))
        elif isinstance(block, ToolResultBlock):
            tokens += estimate_text_tokens(block.content)
        else:
            tokens += 256
    return tokens


@dataclass(frozen=True, slots=True)
class ContextBudget:
    estimated_tokens: int
    compaction_at: int
    reserved_output_tokens: int
    remaining_tokens: int
    should_compact: bool


class TokenEstimator:
    def __init__(
        self,
        window_tokens: int,
        *,
        compaction_threshold: float = 0.8,
        reserved_output_tokens: int = 4_096,
    ) -> None:
        if window_tokens <= reserved_output_tokens:
            raise ValueError("context window must exceed the reserved output budget")
        if not 0 < compaction_threshold < 1:
            raise ValueError("compaction threshold must be between zero and one")
        self.window_tokens = window_tokens
        self.compaction_threshold = compaction_threshold
        self.reserved_output_tokens = reserved_output_tokens

    def estimate(
        self,
        request: ModelRequest,
        *,
        actual_usage: Usage | None = None,
        incremental_text: str = "",
    ) -> ContextBudget:
        estimated = (
            actual_usage.input_tokens
            if actual_usage is not None and actual_usage.input_tokens > 0
            else estimate_text_tokens(request.system_prompt)
            + sum(estimate_message_tokens(message) for message in request.messages)
            + sum(estimate_text_tokens(str(tool.parameters)) + 8 for tool in request.tools)
        )
        estimated += estimate_text_tokens(incremental_text)
        compaction_at = min(
            int(self.window_tokens * self.compaction_threshold),
            self.window_tokens - self.reserved_output_tokens,
        )
        remaining = max(0, self.window_tokens - self.reserved_output_tokens - estimated)
        return ContextBudget(
            estimated_tokens=estimated,
            compaction_at=compaction_at,
            reserved_output_tokens=self.reserved_output_tokens,
            remaining_tokens=remaining,
            should_compact=estimated >= compaction_at,
        )
