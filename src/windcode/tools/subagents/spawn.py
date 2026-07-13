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
    role: SubagentRole = Field(
        description=(
            "researcher and verifier are read-only; use worker only when the child itself must "
            "modify files"
        )
    )
    kind: SubagentTaskKind = Field(
        description="Use read for research/verification and write only for a worker editing files."
    )
    goal: str = Field(min_length=1)
    context: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    verification: tuple[str, ...] = Field(min_length=1)
    allowed_tools: frozenset[str] | None = Field(
        default=None,
        description=(
            "Optional tool restriction. Omit it to use role defaults. Never request write_file, "
            "edit_file, or apply_patch for researcher/verifier read tasks."
        ),
    )
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
        "Create bounded temporary subagents. Researchers and verifiers are read-only and return "
        "their findings to the parent; use a worker/write task only when that child must edit "
        "files. Write tasks use an isolated Git Worktree based on the current parent HEAD, so "
        "never stash, commit, revert, move, or discard parent workspace changes before spawning; "
        "uncommitted parent changes are intentionally not copied into the child Worktree. Omit "
        "allowed_tools unless a narrower tool set is required. Declare "
        "requires_network for network-dependent tasks; network access follows the parent run's "
        "network policy and permission workflow."
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
