from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from windcode.config import AppConfig, PermissionMode, SubagentConfig
from windcode.domain.messages import TextBlock
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta
from windcode.domain.subagents import (
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
)
from windcode.observability import TraceStore
from windcode.providers import ModelTarget
from windcode.runtime.event_bus import EventBus
from windcode.runtime.subagents.coordinator import SubagentCoordinator
from windcode.runtime.subagents.factory import ChildRuntimeFactory
from windcode.runtime.subagents.verification import VerificationRunner
from windcode.sessions import SessionStore
from windcode.tools import create_builtin_registry
from windcode.worktrees import WorktreeManager


class ControlledTransport:
    name = "controlled"

    def __init__(self) -> None:
        self.hanging_started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        block = request.messages[0].content[0]
        assert isinstance(block, TextBlock)
        if "Task: hanging" in block.text:
            self.hanging_started.set()
            await self.release.wait()
        yield TextDelta("complete")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


def task(name: str) -> SubagentTaskSpec:
    return SubagentTaskSpec(
        name,
        SubagentRole.RESEARCHER,
        SubagentTaskKind.READ,
        name,
        "context",
        "result",
        ("evidence",),
    )


def coordinator(tmp_path: Path, transport: ControlledTransport) -> SubagentCoordinator:
    state = tmp_path / "state"
    session = SessionStore.create(state / "sessions", "parent")
    bus = EventBus(session, TraceStore("run", root=state / "traces"))
    target = ModelTarget("controlled", "model", transport)
    config = AppConfig(subagents=SubagentConfig(max_tasks=4, max_concurrent=2))
    factory = ChildRuntimeFactory(
        config=config,
        state_root=state,
        parent_tools=create_builtin_registry(),
        model_chain=lambda _model: (target,),
    )
    return SubagentCoordinator(
        parent_session_id="parent",
        parent_run_id="run",
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        config=config.subagents,
        event_bus=bus,
        factory=factory,
        worktrees=WorktreeManager(worktrees_root=tmp_path / "worktrees"),
        verification=VerificationRunner(),
    )


async def test_running_cancel_isolated_from_completed_sibling(tmp_path: Path) -> None:
    transport = ControlledTransport()
    coord = coordinator(tmp_path, transport)
    hanging, quick = await coord.spawn((task("hanging"), task("quick")))
    await transport.hanging_started.wait()
    quick_result = await coord.wait(quick.subagent_id)
    assert quick_result.status is SubagentStatus.COMPLETED

    await coord.cancel(hanging.subagent_id)
    hanging_result = await coord.wait(hanging.subagent_id)
    assert hanging_result.status is SubagentStatus.CANCELLED
    assert coord.list()[1].status is SubagentStatus.COMPLETED
