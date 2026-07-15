from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.subagents import CollaborationContribution, SubagentMessage
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.runtime.subagents.collaboration import (
    BoundSubagentCollaboration,
    SubagentCollaborationError,
)
from windcode.tools.registry import ToolRegistry


class ListAgentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListAgentsTool:
    name = "list_agents"
    description = "List sibling agents in this parent run for peer collaboration."
    input_model = ListAgentsInput
    effects = frozenset({ToolEffect.READ})

    def __init__(self, collaboration: BoundSubagentCollaboration) -> None:
        self.collaboration = collaboration

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context, arguments
        data = [
            {
                "subagent_id": record.subagent_id,
                "task_name": record.spec.task_name,
                "role": record.spec.role.value,
                "kind": record.spec.kind.value,
                "status": record.status.value,
                "is_self": record.subagent_id == self.collaboration.subagent_id,
            }
            for record in self.collaboration.list_agents()
        ]
        return ToolResult(json.dumps(data, ensure_ascii=True), data={"agents": data})


class SendMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=8_000)


class SendMessageTool:
    name = "send_message"
    description = "Send a text message to a queued or running sibling subagent by task name or ID."
    input_model = SendMessageInput
    effects = frozenset({ToolEffect.AGENT_COMMUNICATION})

    def __init__(self, collaboration: BoundSubagentCollaboration) -> None:
        self.collaboration = collaboration

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(SendMessageInput, arguments)
        try:
            message = await self.collaboration.send(parsed.target, parsed.message)
        except SubagentCollaborationError as exc:
            return ToolResult(str(exc), is_error=True, data={"error": exc.category})
        data = {
            "message_id": message.message_id,
            "recipient_subagent_id": message.recipient_subagent_id,
            "recipient_task_name": message.recipient_task_name,
            "status": "accepted",
        }
        return ToolResult(json.dumps(data, ensure_ascii=True), data=data)


class WaitForMessagesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=60.0, gt=0, le=300.0)
    max_messages: int = Field(default=20, ge=1, le=20)


def _message_data(message: SubagentMessage) -> dict[str, object]:
    return {
        "message_id": message.message_id,
        "sender_subagent_id": message.sender_subagent_id,
        "sender_task_name": message.sender_task_name,
        "message": message.content,
        "created_at": message.created_at.isoformat(),
        "delivered_at": (
            None if message.delivered_at is None else message.delivered_at.isoformat()
        ),
    }


class WaitForMessagesTool:
    name = "wait_for_messages"
    description = (
        "Wait once for sibling messages. A timeout returns an empty successful result; do not poll."
    )
    input_model = WaitForMessagesInput
    effects = frozenset({ToolEffect.AGENT_COMMUNICATION})

    def __init__(self, collaboration: BoundSubagentCollaboration) -> None:
        self.collaboration = collaboration

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(WaitForMessagesInput, arguments)
        messages = await self.collaboration.wait(
            timeout_seconds=parsed.timeout_seconds,
            max_messages=parsed.max_messages,
        )
        data = [_message_data(message) for message in messages]
        return ToolResult(json.dumps(data, ensure_ascii=True), data={"messages": data})


class ExchangeRoundInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_index: int = Field(ge=0, le=3)
    contribution: str = Field(min_length=1, max_length=12_000)
    timeout_seconds: float = Field(default=300.0, gt=0, le=600.0)


def _contribution_data(item: CollaborationContribution) -> dict[str, object]:
    return {
        "participant_name": item.participant_name,
        "round_index": item.round_index,
        "subagent_id": item.subagent_id,
        "content": item.content,
    }


class ExchangeRoundTool:
    name = "exchange_round"
    description = (
        "Submit this participant's contribution for one coordinated round and wait at the barrier "
        "until every team member submits. Returns all same-round contributions exactly once."
    )
    input_model = ExchangeRoundInput
    effects = frozenset({ToolEffect.AGENT_COMMUNICATION})

    def __init__(self, collaboration: BoundSubagentCollaboration) -> None:
        self.collaboration = collaboration

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(ExchangeRoundInput, arguments)
        try:
            contributions = await self.collaboration.exchange_round(
                parsed.round_index,
                parsed.contribution,
                parsed.timeout_seconds,
            )
        except SubagentCollaborationError as exc:
            return ToolResult(str(exc), is_error=True, data={"error": exc.category})
        data = [_contribution_data(item) for item in contributions]
        return ToolResult(json.dumps(data, ensure_ascii=True), data={"contributions": data})


def register_collaboration_tools(
    registry: ToolRegistry,
    collaboration: BoundSubagentCollaboration,
) -> None:
    for tool in (
        ListAgentsTool(collaboration),
        SendMessageTool(collaboration),
        WaitForMessagesTool(collaboration),
    ):
        registry.register(tool)


def register_coordination_tool(
    registry: ToolRegistry,
    collaboration: BoundSubagentCollaboration,
) -> None:
    registry.register(ExchangeRoundTool(collaboration))
