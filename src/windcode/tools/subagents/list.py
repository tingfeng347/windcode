from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.runtime.subagents.coordinator import SubagentCoordinator


class ListSubagentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListSubagentsTool:
    name = "list_subagents"
    description = (
        "Take a non-blocking subagent status snapshot for explicit inspection. "
        "Do not poll this tool; use wait_subagents to await completion."
    )
    input_model = ListSubagentsInput
    effects = frozenset({ToolEffect.READ})

    def __init__(self, coordinator: SubagentCoordinator) -> None:
        self.coordinator = coordinator

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context, arguments
        data = [
            {
                "subagent_id": record.subagent_id,
                "task_index": record.task_index,
                "task_name": record.spec.task_name,
                "role": record.spec.role.value,
                "kind": record.spec.kind.value,
                "status": record.status.value,
                "commit": record.commit,
                "worktree_path": (
                    None if record.worktree_path is None else str(record.worktree_path)
                ),
                "error_category": record.error_category,
                "error_message": record.error_message,
            }
            for record in self.coordinator.list()
        ]
        return ToolResult(json.dumps(data, ensure_ascii=True), data={"subagents": data})
