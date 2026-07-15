from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from windcode.domain.messages import Message, Role, TextBlock
from windcode.domain.subagents import (
    CollaborationContribution,
    CollaborationMode,
    SubagentMessage,
    SubagentRecord,
)


class SubagentCollaborationError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        self.category = category
        super().__init__(message)


class CollaborationBackend(Protocol):
    def list_peers(self, sender_subagent_id: str) -> tuple[SubagentRecord, ...]: ...

    async def send_message(
        self,
        sender_subagent_id: str,
        target: str,
        content: str,
    ) -> SubagentMessage: ...

    async def receive_messages(
        self,
        recipient_subagent_id: str,
        *,
        max_messages: int,
        timeout_seconds: float | None = None,
        close_if_empty: bool = False,
    ) -> tuple[SubagentMessage, ...]: ...

    async def exchange_coordination_round(
        self,
        subagent_id: str,
        round_index: int,
        contribution: str,
        timeout_seconds: float,
    ) -> tuple[CollaborationContribution, ...]: ...


@dataclass(slots=True)
class CoordinationSession:
    collaboration_id: str
    mode: CollaborationMode
    participant_names: tuple[str, ...]
    rounds: int
    contributions: dict[int, dict[str, CollaborationContribution]] = field(
        default_factory=dict[int, dict[str, CollaborationContribution]]
    )
    aborted_reason: str | None = None


def format_inbound_message(message: SubagentMessage) -> Message:
    return Message(
        Role.USER,
        (
            TextBlock(
                "[Message from sibling subagent "
                f"{message.sender_task_name} ({message.sender_subagent_id})]\n"
                f"{message.content}"
            ),
        ),
        provider_metadata={
            "subagent_message_id": message.message_id,
            "sender_subagent_id": message.sender_subagent_id,
        },
    )


@dataclass(frozen=True, slots=True)
class BoundSubagentCollaboration:
    backend: CollaborationBackend
    subagent_id: str

    def list_agents(self) -> tuple[SubagentRecord, ...]:
        return self.backend.list_peers(self.subagent_id)

    async def send(self, target: str, content: str) -> SubagentMessage:
        return await self.backend.send_message(self.subagent_id, target, content)

    async def wait(
        self,
        *,
        timeout_seconds: float,
        max_messages: int,
    ) -> tuple[SubagentMessage, ...]:
        return await self.backend.receive_messages(
            self.subagent_id,
            max_messages=max_messages,
            timeout_seconds=timeout_seconds,
        )

    async def drain_inbound(self) -> tuple[Message, ...]:
        messages = await self.backend.receive_messages(
            self.subagent_id,
            max_messages=100,
        )
        return tuple(format_inbound_message(message) for message in messages)

    async def drain_or_close_inbound(self) -> tuple[Message, ...]:
        messages = await self.backend.receive_messages(
            self.subagent_id,
            max_messages=100,
            close_if_empty=True,
        )
        return tuple(format_inbound_message(message) for message in messages)

    async def exchange_round(
        self,
        round_index: int,
        contribution: str,
        timeout_seconds: float,
    ) -> tuple[CollaborationContribution, ...]:
        return await self.backend.exchange_coordination_round(
            self.subagent_id,
            round_index,
            contribution,
            timeout_seconds,
        )
