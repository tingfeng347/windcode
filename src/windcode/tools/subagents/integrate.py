from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.runtime.subagents.coordinator import SubagentCoordinator, SubagentCoordinatorError


class IntegrateSubagentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_id: str = Field(min_length=1)
    verification_commands: tuple[str, ...] = ()


class IntegrateSubagentTool:
    name = "integrate_subagent"
    description = "Integrate a completed subagent commit and verify it in the parent workspace."
    input_model = IntegrateSubagentInput
    effects = frozenset({ToolEffect.WORKSPACE_WRITE, ToolEffect.PROCESS})

    def __init__(self, coordinator: SubagentCoordinator) -> None:
        self.coordinator = coordinator

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(IntegrateSubagentInput, arguments)
        try:
            result = await self.coordinator.integrate(
                parsed.subagent_id, parsed.verification_commands
            )
        except SubagentCoordinatorError as exc:
            return ToolResult(output=str(exc), is_error=True, data={"error": exc.category})
        data = {
            "subagent_id": result.subagent_id,
            "status": result.status.value,
            "commit": result.commit,
            "verification": [
                {
                    "command": item.command,
                    "exit_code": item.exit_code,
                    "passed": item.passed,
                    "output_summary": item.output_summary,
                }
                for item in result.verification
            ],
            "error_category": result.error_category,
            "error_message": result.error_message,
        }
        is_error = result.error_category is not None
        return ToolResult(json.dumps(data, ensure_ascii=True), is_error=is_error, data=data)
