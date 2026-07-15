from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ReasoningBlock:
    summary: str
    opaque: dict[str, Any] = field(default_factory=dict[str, Any], repr=False)


@dataclass(frozen=True, slots=True)
class ToolCallBlock:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    call_id: str
    name: str
    content: str
    is_error: bool = False
    artifact_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AttachmentBlock:
    media_type: str
    reference: str
    description: str | None = None


ContentBlock = TextBlock | ReasoningBlock | ToolCallBlock | ToolResultBlock | AttachmentBlock


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: tuple[ContentBlock, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provider_metadata: dict[str, Any] = field(default_factory=dict[str, Any], repr=False)


@dataclass(frozen=True, slots=True)
class SourcedContextMessage:
    source_id: str
    content: str
    lifecycle: str = "next_model_step"


def message_to_dict(message: Message) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ReasoningBlock):
            content.append({"type": "reasoning", "summary": block.summary, "opaque": block.opaque})
        elif isinstance(block, ToolCallBlock):
            content.append(
                {
                    "type": "tool_call",
                    "call_id": block.call_id,
                    "name": block.name,
                    "arguments": block.arguments,
                }
            )
        elif isinstance(block, ToolResultBlock):
            content.append(
                {
                    "type": "tool_result",
                    "call_id": block.call_id,
                    "name": block.name,
                    "content": block.content,
                    "is_error": block.is_error,
                    "artifact_ref": block.artifact_ref,
                }
            )
        else:
            content.append(
                {
                    "type": "attachment",
                    "media_type": block.media_type,
                    "reference": block.reference,
                    "description": block.description,
                }
            )
    return {
        "role": message.role.value,
        "content": content,
        "created_at": message.created_at.isoformat(),
        "provider_metadata": message.provider_metadata,
    }


def heal_dangling_tool_calls(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Normalize assistant tool calls and tool results into provider-safe groups.

    A run that is cancelled or fails after the assistant tool_calls message is
    persisted but before tool results are recorded leaves a dangling tool call.
    Context compaction can also retain a tool result after dropping its assistant
    call. OpenAI-compatible providers reject both shapes, so discard orphan or
    duplicate results and synthesize an error result for every unanswered call.
    """

    healed: list[Message] = []
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        call_blocks = [block for block in message.content if isinstance(block, ToolCallBlock)]
        if message.role is not Role.ASSISTANT or not call_blocks:
            if message.role is not Role.TOOL:
                healed.append(message)
            index += 1
            continue
        healed.append(message)
        required = {block.call_id: block.name for block in call_blocks}
        answered: set[str] = set()
        cursor = index + 1
        while cursor < total and messages[cursor].role is Role.TOOL:
            valid_results = tuple(
                block
                for block in messages[cursor].content
                if isinstance(block, ToolResultBlock)
                and block.call_id in required
                and block.call_id not in answered
            )
            if valid_results:
                healed.append(replace(messages[cursor], content=valid_results))
                answered.update(block.call_id for block in valid_results)
            cursor += 1
        missing = [call_id for call_id in required if call_id not in answered]
        if missing:
            healed.append(
                Message(
                    Role.TOOL,
                    tuple(
                        ToolResultBlock(
                            call_id,
                            required[call_id],
                            "Tool call was interrupted before it produced a result.",
                            is_error=True,
                        )
                        for call_id in missing
                    ),
                )
            )
        index = cursor
    return tuple(healed)


def _object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("message value must be an object")
    raw = cast(Mapping[object, object], value)
    return {str(key): item for key, item in raw.items()}


def message_from_dict(value: Mapping[str, object]) -> Message:
    raw_content = value.get("content")
    if not isinstance(raw_content, list):
        raise ValueError("message content must be a list")
    blocks: list[ContentBlock] = []
    for raw_block in cast(list[object], raw_content):
        block = _object_mapping(raw_block)
        block_type = str(block.get("type", ""))
        if block_type == "text":
            blocks.append(TextBlock(str(block.get("text", ""))))
        elif block_type == "reasoning":
            blocks.append(
                ReasoningBlock(
                    str(block.get("summary", "")),
                    cast(dict[str, Any], _object_mapping(block.get("opaque", {}))),
                )
            )
        elif block_type == "tool_call":
            blocks.append(
                ToolCallBlock(
                    str(block.get("call_id", "")),
                    str(block.get("name", "")),
                    cast(dict[str, Any], _object_mapping(block.get("arguments", {}))),
                )
            )
        elif block_type == "tool_result":
            artifact = block.get("artifact_ref")
            blocks.append(
                ToolResultBlock(
                    str(block.get("call_id", "")),
                    str(block.get("name", "")),
                    str(block.get("content", "")),
                    bool(block.get("is_error", False)),
                    None if artifact is None else str(artifact),
                )
            )
        elif block_type == "attachment":
            description = block.get("description")
            blocks.append(
                AttachmentBlock(
                    str(block.get("media_type", "application/octet-stream")),
                    str(block.get("reference", "")),
                    None if description is None else str(description),
                )
            )
        else:
            raise ValueError(f"unknown content block type: {block_type}")
    metadata = _object_mapping(value.get("provider_metadata", {}))
    created = value.get("created_at")
    return Message(
        role=Role(str(value["role"])),
        content=tuple(blocks),
        created_at=(
            datetime.fromisoformat(created) if isinstance(created, str) else datetime.now(UTC)
        ),
        provider_metadata=cast(dict[str, Any], metadata),
    )
