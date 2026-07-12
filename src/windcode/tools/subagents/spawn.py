from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.subagents import SubagentRole, SubagentTaskKind, SubagentTaskSpec
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.runtime.subagents.coordinator import SubagentCoordinator, SubagentCoordinatorError


class SubagentTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_name: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    role: SubagentRole
    kind: SubagentTaskKind
    goal: str = Field(min_length=1)
    context: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    verification: tuple[str, ...] = Field(min_length=1)
    allowed_tools: frozenset[str] | None = None
    model: str | None = None
    requires_network: bool = Field(
        default=False,
        description="Whether the task requires external network access.",
    )

    def to_spec(self) -> SubagentTaskSpec:
        return SubagentTaskSpec(
            self.task_name,
            self.role,
            self.kind,
            self.goal,
            self.context,
            self.expected_output,
            self.verification,
            self.allowed_tools,
            self.model,
            self.requires_network,
        )


class SpawnSubagentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: tuple[SubagentTaskInput, ...] = Field(min_length=1, max_length=16)


class SpawnSubagentsTool:
    name = "spawn_subagents"
    description = (
        "Create bounded temporary subagents. Declare requires_network for network-dependent "
        "tasks; network access follows the parent run's network policy and permission workflow."
    )
    input_model = SpawnSubagentsInput
    effects = frozenset({ToolEffect.PROCESS, ToolEffect.WORKSPACE_WRITE})

    def __init__(self, coordinator: SubagentCoordinator) -> None:
        self.coordinator = coordinator

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(SpawnSubagentsInput, arguments)
        try:
            records = await self.coordinator.spawn(tuple(item.to_spec() for item in parsed.tasks))
        except (SubagentCoordinatorError, ValueError) as exc:
            category = getattr(exc, "category", "invalid_task")
            return ToolResult(
                output=str(exc),
                is_error=True,
                data={"error": str(category)},
            )
        data = [
            {
                "subagent_id": record.subagent_id,
                "task_index": record.task_index,
                "task_name": record.spec.task_name,
                "role": record.spec.role.value,
                "kind": record.spec.kind.value,
                "status": record.status.value,
            }
            for record in records
        ]
        return ToolResult(json.dumps(data, ensure_ascii=True), data={"subagents": data})
