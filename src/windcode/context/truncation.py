from __future__ import annotations

from dataclasses import dataclass, replace

from windcode.domain.messages import (
    AttachmentBlock,
    ContentBlock,
    Message,
    Role,
    ToolCallBlock,
    ToolResultBlock,
)
from windcode.sessions import ArtifactReference, ArtifactStore


@dataclass(frozen=True, slots=True)
class TruncationResult:
    messages: tuple[Message, ...]
    artifacts: tuple[ArtifactReference, ...]
    removed_attachments: int
    changed: bool


def _recent_cutoff(messages: tuple[Message, ...], preserve_recent_turns: int) -> int:
    user_indexes = [index for index, message in enumerate(messages) if message.role is Role.USER]
    if len(user_indexes) <= preserve_recent_turns:
        return 0
    return user_indexes[-preserve_recent_turns]


def truncate_context(
    messages: tuple[Message, ...],
    artifact_store: ArtifactStore,
    *,
    max_tool_result_chars: int = 20_000,
    preserve_recent_turns: int = 8,
) -> TruncationResult:
    cutoff = _recent_cutoff(messages, preserve_recent_turns)
    completed_calls = {
        block.call_id
        for message in messages
        for block in message.content
        if isinstance(block, ToolResultBlock)
    }
    open_calls = {
        block.call_id
        for message in messages
        for block in message.content
        if isinstance(block, ToolCallBlock) and block.call_id not in completed_calls
    }
    artifacts: list[ArtifactReference] = []
    removed = 0
    changed = False
    transformed: list[Message] = []
    for index, message in enumerate(messages):
        protected = (
            index >= cutoff
            or message.role is Role.SYSTEM
            or any(
                isinstance(block, ToolCallBlock) and block.call_id in open_calls
                for block in message.content
            )
        )
        content: list[ContentBlock] = []
        for block in message.content:
            if isinstance(block, AttachmentBlock) and not protected:
                removed += 1
                changed = True
                continue
            if (
                isinstance(block, ToolResultBlock)
                and not protected
                and len(block.content) > max_tool_result_chars
            ):
                summary, reference = artifact_store.externalize(
                    block.content, threshold=max_tool_result_chars
                )
                if reference is not None:
                    artifacts.append(reference)
                    content.append(
                        replace(block, content=summary, artifact_ref=reference.relative_path)
                    )
                    changed = True
                    continue
            content.append(block)
        transformed.append(replace(message, content=tuple(content)))
    return TruncationResult(tuple(transformed), tuple(artifacts), removed, changed)
