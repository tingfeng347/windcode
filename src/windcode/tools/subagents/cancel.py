from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.runtime.subagents.coordinator import SubagentCoordinator, SubagentCoordinatorError


class CancelSubagentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_id: str = Field(min_length=1)


class CancelSubagentTool:
    name = "cancel_subagent"
    description = "Cancel one queued or running temporary subagent."
    input_model = CancelSubagentInput
    effects = frozenset({ToolEffect.PROCESS})

    def __init__(self, coordinator: SubagentCoordinator) -> None:
        self.coordinator = coordinator

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(CancelSubagentInput, arguments)
        try:
            record = await self.coordinator.cancel(parsed.subagent_id)
        except SubagentCoordinatorError as exc:
            return ToolResult(output=str(exc), is_error=True, data={"error": exc.category})
        data = {"subagent_id": record.subagent_id, "status": record.status.value}
        return ToolResult(json.dumps(data, ensure_ascii=True), data=data)
