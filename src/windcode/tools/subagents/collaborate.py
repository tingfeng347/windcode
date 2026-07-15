from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.subagents import (
    CollaborationMode,
    CollaborationParticipant,
    CollaborationRequest,
    CollaborationResult,
    SubagentRole,
    SubagentTaskKind,
)
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.runtime.subagents.coordinator import SubagentCoordinator, SubagentCoordinatorError
from windcode.runtime.subagents.teamwork import run_collaboration


class CollaborationParticipantInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    assignment: str = Field(
        min_length=1,
        description="This participant's distinct responsibility, workstream, or perspective.",
    )
    role: SubagentRole = SubagentRole.RESEARCHER
    kind: SubagentTaskKind = SubagentTaskKind.READ
    allowed_tools: frozenset[str] | None = None
    model: str | None = None
    requires_network: bool = False

    def to_domain(self) -> CollaborationParticipant:
        return CollaborationParticipant(
            name=self.name,
            assignment=self.assignment,
            role=self.role,
            kind=self.kind,
            allowed_tools=self.allowed_tools,
            model=self.model,
            requires_network=self.requires_network,
        )


class CollaborateSubagentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(
        min_length=1,
        description="The user's original natural-language collaboration request.",
    )
    context: str = ""
    mode: CollaborationMode = Field(
        default=CollaborationMode.AUTO,
        description=(
            "Use auto to infer negotiation, division, or hybrid from natural language. Choose "
            "negotiation for competing positions, division for parallel workstreams, and hybrid "
            "when parallel deliverables also require mutual challenge or conflict resolution."
        ),
    )
    participants: tuple[CollaborationParticipantInput, ...] = Field(min_length=2, max_length=8)
    rounds: int = Field(default=2, ge=1, le=3)
    synthesis_instructions: str = Field(
        default=(
            "Attribute contributions, identify consensus and unresolved conflicts, check "
            "dependencies, and provide an integrated recommendation or delivery plan."
        ),
        min_length=1,
    )

    def to_domain(self) -> CollaborationRequest:
        return CollaborationRequest(
            request=self.request,
            context=self.context,
            participants=tuple(participant.to_domain() for participant in self.participants),
            mode=self.mode,
            rounds=self.rounds,
            synthesis_instructions=self.synthesis_instructions,
        )


def _result_data(result: CollaborationResult) -> dict[str, object]:
    return {
        "collaboration_id": result.collaboration_id,
        "request": result.request,
        "mode": result.mode.value,
        "status": result.status,
        "contributions": [
            {
                "participant_name": item.participant_name,
                "phase": item.phase,
                "round_index": item.round_index,
                "subagent_id": item.subagent_id,
                "content": item.content,
            }
            for item in result.contributions
        ],
        "participant_results": [
            {
                "subagent_id": item.subagent_id,
                "task_name": item.task_name,
                "status": item.status.value,
                "summary": item.summary,
                "changed_files": list(item.changed_files),
                "commit": item.commit,
                "error_category": item.error_category,
                "error_message": item.error_message,
            }
            for item in result.participant_results
        ],
        "synthesis": result.synthesis,
        "synthesizer_subagent_id": result.synthesizer_subagent_id,
        "error_category": result.error_category,
        "error_message": result.error_message,
    }


class CollaborateSubagentsTool:
    name = "collaborate_subagents"
    description = (
        "Coordinate two to eight simultaneous subagents from a natural-language request. Auto "
        "detects negotiation, division-of-work, or hybrid mode; assigns stable participants to "
        "parallel workstreams, enforces synchronized multi-round exchange, and uses an independent "
        "final synthesizer. Use this instead of spawn_subagents when peers must react to each "
        "other or combine dependent deliverables."
    )
    input_model = CollaborateSubagentsInput
    effects = frozenset({ToolEffect.PROCESS, ToolEffect.WORKSPACE_WRITE})

    def __init__(self, coordinator: SubagentCoordinator) -> None:
        self.coordinator = coordinator

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(CollaborateSubagentsInput, arguments)
        try:
            result = await run_collaboration(self.coordinator, parsed.to_domain())
        except (SubagentCoordinatorError, ValueError) as exc:
            category = getattr(exc, "category", "invalid_collaboration")
            return ToolResult(str(exc), is_error=True, data={"error": str(category)})
        data = _result_data(result)
        return ToolResult(
            json.dumps(data, ensure_ascii=True),
            is_error=result.status != "completed",
            data=data,
        )
