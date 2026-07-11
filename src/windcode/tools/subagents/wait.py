from __future__ import annotations

import asyncio
import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.subagents import SubagentResult
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.runtime.subagents.coordinator import SubagentCoordinator, SubagentCoordinatorError


class WaitSubagentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    timeout_seconds: float = Field(default=300.0, gt=0, le=900.0)


def _result_data(result: SubagentResult) -> dict[str, object]:
    return {
        "subagent_id": result.subagent_id,
        "task_name": result.task_name,
        "status": result.status.value,
        "summary": result.summary,
        "changed_files": list(result.changed_files),
        "commit": result.commit,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cache_read_tokens": result.usage.cache_read_tokens,
            "cache_write_tokens": result.usage.cache_write_tokens,
        },
        "error_category": result.error_category,
        "error_message": result.error_message,
    }


class WaitSubagentsTool:
    name = "wait_subagents"
    description = (
        "Wait once for temporary subagents to reach terminal results. Use this after spawning; "
        "do not poll list_subagents."
    )
    input_model = WaitSubagentsInput
    effects = frozenset({ToolEffect.READ})

    def __init__(self, coordinator: SubagentCoordinator) -> None:
        self.coordinator = coordinator

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(WaitSubagentsInput, arguments)
        try:
            async with asyncio.timeout(parsed.timeout_seconds):
                results = await asyncio.gather(
                    *(self.coordinator.wait(subagent_id) for subagent_id in parsed.subagent_ids)
                )
        except TimeoutError:
            data = {
                "error": "wait_timeout",
                "subagent_ids": list(parsed.subagent_ids),
                "statuses": {
                    record.subagent_id: record.status.value
                    for record in self.coordinator.list()
                    if record.subagent_id in parsed.subagent_ids
                },
            }
            return ToolResult(json.dumps(data, ensure_ascii=True), is_error=True, data=data)
        except SubagentCoordinatorError as exc:
            return ToolResult(output=str(exc), is_error=True, data={"error": exc.category})
        data = [_result_data(result) for result in results]
        return ToolResult(json.dumps(data, ensure_ascii=True), data={"subagents": data})
