import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode.config import PermissionMode
from windcode.domain.events import (
    AgentEventType,
    ApprovalRequested,
    ApprovalResponse,
    RunCancelled,
)
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
)
from windcode.observability import TraceStore
from windcode.policy import PolicyEngine
from windcode.providers import ModelTarget
from windcode.runtime import AgentLoop, EventBus, RunControl, ToolScheduler
from windcode.sessions import SessionStore
from windcode.tools import ToolRegistry
from windcode.tools.write_file import WriteFileTool


class WriteTransport:
    name = "scripted"

    def __init__(self) -> None:
        self.step = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        self.step += 1
        if self.step == 1:
            yield ToolCallDelta("write", "write_file", '{"path":"file.txt","content":"new"}')
            yield ModelCompleted(StopReason.TOOL_USE)
        else:
            yield TextDelta("finished")
            yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


def build_loop(tmp_path: Path) -> tuple[AgentLoop, EventBus, RunControl]:
    session = SessionStore.create(tmp_path / "sessions", "session")
    bus = EventBus(session, TraceStore("run", root=tmp_path / "traces"))
    registry = ToolRegistry()
    registry.register(WriteFileTool())
    control = RunControl()
    scheduler = ToolScheduler(registry, PolicyEngine(PermissionMode.DEFAULT))
    loop = AgentLoop(
        session_id="session",
        run_id="run",
        model_chain=(ModelTarget("scripted", "model", WriteTransport()),),
        scheduler=scheduler,
        control=control,
        event_bus=bus,
        system_prompt="system",
    )
    return loop, bus, control


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_content"),
    [("allow_once", "new"), ("deny", "original")],
)
async def test_approval_controls_side_effect(
    tmp_path: Path, decision: str, expected_content: str
) -> None:
    path = tmp_path / "file.txt"
    path.write_text("original")
    loop, bus, control = build_loop(tmp_path)
    task = asyncio.create_task(loop.run("write", tmp_path))

    async for event in bus.subscribe():
        if isinstance(event, ApprovalRequested):
            control.respond(ApprovalResponse(event.request_id, decision))

    result = await task
    assert result.final_text == "finished"
    assert path.read_text() == expected_content


@pytest.mark.asyncio
async def test_cancelling_during_approval_produces_cancelled_terminal(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("original")
    loop, bus, _control = build_loop(tmp_path)
    task = asyncio.create_task(loop.run("write", tmp_path))
    events: list[AgentEventType] = []

    async for event in bus.subscribe():
        events.append(event)
        if isinstance(event, ApprovalRequested):
            task.cancel()

    result = await task
    assert result.status == "cancelled"
    assert (tmp_path / "file.txt").read_text() == "original"
    assert isinstance(events[-1], RunCancelled)
