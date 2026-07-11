import asyncio
from pathlib import Path
from time import monotonic
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict

from windcode.config import PermissionMode
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.policy import ApprovalChoice, PolicyDecision, PolicyEngine, PolicyRequest
from windcode.runtime import ScheduledCall, ToolScheduler
from windcode.tools import ToolRegistry


class DelayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    delay: float = 0.05


class DelayTool:
    description = "Delay for a test."
    input_model = DelayInput

    def __init__(
        self, name: str, effects: frozenset[ToolEffect], timeline: list[tuple[str, str]]
    ) -> None:
        self.name = name
        self.effects = effects
        self.timeline = timeline

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(DelayInput, arguments)
        self.timeline.append(("start", parsed.label))
        await asyncio.sleep(parsed.delay)
        self.timeline.append(("end", parsed.label))
        return ToolResult(parsed.label)


def setup_scheduler(
    tmp_path: Path, mode: PermissionMode = PermissionMode.FULL_ACCESS
) -> tuple[ToolScheduler, ToolContext, list[tuple[str, str]]]:
    timeline: list[tuple[str, str]] = []
    registry = ToolRegistry()
    registry.register(DelayTool("read", frozenset({ToolEffect.READ}), timeline))
    registry.register(DelayTool("write", frozenset({ToolEffect.WORKSPACE_WRITE}), timeline))
    return (
        ToolScheduler(registry, PolicyEngine(mode, sandbox_enabled=False)),
        ToolContext(tmp_path, "run", lambda: False),
        timeline,
    )


@pytest.mark.asyncio
async def test_consecutive_reads_run_concurrently_and_keep_result_order(tmp_path: Path) -> None:
    scheduler, context, _ = setup_scheduler(tmp_path)
    started = monotonic()
    results = await scheduler.execute(
        (
            ScheduledCall("one", "read", {"label": "one", "delay": 0.08}),
            ScheduledCall("two", "read", {"label": "two", "delay": 0.02}),
        ),
        context,
    )

    assert monotonic() - started < 0.13
    assert [item.call_id for item in results] == ["one", "two"]
    assert [item.result.output for item in results] == ["one", "two"]
    assert all(item.result.elapsed_seconds > 0 for item in results)


@pytest.mark.asyncio
async def test_writes_are_exclusive_and_ordered(tmp_path: Path) -> None:
    scheduler, context, timeline = setup_scheduler(tmp_path)
    await scheduler.execute(
        (
            ScheduledCall("one", "write", {"label": "one", "delay": 0.01}),
            ScheduledCall("two", "write", {"label": "two", "delay": 0.01}),
        ),
        context,
    )
    assert timeline == [("start", "one"), ("end", "one"), ("start", "two"), ("end", "two")]


@pytest.mark.asyncio
async def test_denied_approval_has_no_side_effect(tmp_path: Path) -> None:
    timeline: list[tuple[str, str]] = []
    registry = ToolRegistry()
    registry.register(DelayTool("write", frozenset({ToolEffect.WORKSPACE_WRITE}), timeline))

    async def deny(_request: PolicyRequest, _decision: PolicyDecision) -> ApprovalChoice:
        return ApprovalChoice.DENY

    scheduler = ToolScheduler(registry, PolicyEngine(PermissionMode.DEFAULT), approval_handler=deny)
    results = await scheduler.execute(
        (ScheduledCall("one", "write", {"label": "one"}),),
        ToolContext(tmp_path, "run", lambda: False),
    )
    assert results[0].result.data["error"] == "approval_denied"
    assert timeline == []
