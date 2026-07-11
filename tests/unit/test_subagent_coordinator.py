from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from windcode.config import PermissionMode, SubagentConfig
from windcode.domain.events import RunResult
from windcode.domain.subagents import (
    SubagentRecord,
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
    subagent_record_to_dict,
)
from windcode.observability import TraceStore
from windcode.runtime.control import RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.loop import AgentLoop
from windcode.runtime.subagents.coordinator import (
    SubagentCoordinator,
    SubagentCoordinatorError,
)
from windcode.runtime.subagents.factory import ChildRuntime, ChildRuntimeFactory
from windcode.runtime.subagents.verification import VerificationRunner
from windcode.sessions import SessionStore
from windcode.worktrees import WorktreeManager


def task(name: str) -> SubagentTaskSpec:
    return SubagentTaskSpec(
        name,
        SubagentRole.RESEARCHER,
        SubagentTaskKind.READ,
        f"run {name}",
        "self-contained context",
        "result",
        ("return evidence",),
    )


class FakeLoop:
    def __init__(self, factory: FakeFactory, name: str, event_bus: EventBus) -> None:
        self.factory = factory
        self.name = name
        self.event_bus = event_bus

    async def run(self, prompt: str, workspace: Path) -> RunResult:
        del prompt, workspace
        self.factory.active += 1
        self.factory.peak = max(self.factory.peak, self.factory.active)
        self.factory.started.append(self.name)
        self.factory.started_event.set()
        try:
            await self.factory.gates[self.name].wait()
            return RunResult(status="completed", final_text=f"completed {self.name}")
        finally:
            self.factory.active -= 1
            await self.event_bus.close()


class FakeFactory:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []
        self.started_event = asyncio.Event()
        self.active = 0
        self.peak = 0

    def create(self, record: SubagentRecord, **kwargs: object) -> ChildRuntime:
        workspace = cast(Path, kwargs["workspace"])
        name = record.spec.task_name
        self.gates.setdefault(name, asyncio.Event())
        session_id = f"child-{name}"
        child_record = replace(record, child_session_id=session_id)
        session = SessionStore.create(self.tmp_path / "child-sessions", session_id)
        bus = EventBus(session, TraceStore(session_id, root=self.tmp_path / "child-traces"))
        loop = cast(AgentLoop, FakeLoop(self, name, bus))
        return ChildRuntime(child_record, RunControl(), bus, loop, workspace, name)


def coordinator(
    tmp_path: Path,
    *,
    config: SubagentConfig | None = None,
    factory: FakeFactory | None = None,
    bus: EventBus | None = None,
) -> tuple[SubagentCoordinator, FakeFactory, EventBus]:
    child_factory = factory or FakeFactory(tmp_path)
    if bus is None:
        session = SessionStore.create(tmp_path / "parent-sessions", "parent")
        bus = EventBus(session, TraceStore("parent", root=tmp_path / "parent-traces"))
    instance = SubagentCoordinator(
        parent_session_id="parent",
        parent_run_id="run",
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        config=config or SubagentConfig(max_tasks=8, max_concurrent=2),
        event_bus=bus,
        factory=cast(ChildRuntimeFactory, child_factory),
        worktrees=WorktreeManager(worktrees_root=tmp_path / "worktrees"),
        verification=VerificationRunner(),
    )
    return instance, child_factory, bus


async def wait_until_started(factory: FakeFactory, count: int) -> None:
    while len(factory.started) < count:
        factory.started_event.clear()
        await factory.started_event.wait()


async def test_capacity_validation_is_atomic(tmp_path: Path) -> None:
    coord, _, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=2, max_concurrent=2),
    )
    with pytest.raises(SubagentCoordinatorError) as error:
        await coord.spawn((task("one"), task("two"), task("three")))
    assert error.value.category == "capacity_exceeded"
    assert coord.list() == ()

    with pytest.raises(SubagentCoordinatorError) as error:
        await coord.spawn((task("same"), task("same")))
    assert error.value.category == "duplicate_task_name"
    assert coord.list() == ()


async def test_execute_uses_fifo_and_stable_result_order(tmp_path: Path) -> None:
    coord, factory, _ = coordinator(tmp_path)
    records = await coord.spawn((task("first"), task("second"), task("third")))
    await wait_until_started(factory, 2)
    assert factory.started == ["first", "second"]
    assert [record.status for record in coord.list()] == [
        SubagentStatus.RUNNING,
        SubagentStatus.RUNNING,
        SubagentStatus.QUEUED,
    ]

    factory.gates["first"].set()
    await wait_until_started(factory, 3)
    assert factory.started == ["first", "second", "third"]
    factory.gates["second"].set()
    factory.gates["third"].set()
    results = await asyncio.gather(*(coord.wait(record.subagent_id) for record in records))
    assert [result.task_name for result in results] == ["first", "second", "third"]
    assert factory.peak == 2


async def test_default_capacity_runs_four_and_queues_two_fifo(tmp_path: Path) -> None:
    coord, factory, _ = coordinator(tmp_path, config=SubagentConfig())
    specs = tuple(task(f"task_{index}") for index in range(6))
    records = await coord.spawn(specs)
    await wait_until_started(factory, 4)

    assert factory.started == ["task_0", "task_1", "task_2", "task_3"]
    assert [record.status for record in coord.list()] == [
        SubagentStatus.RUNNING,
        SubagentStatus.RUNNING,
        SubagentStatus.RUNNING,
        SubagentStatus.RUNNING,
        SubagentStatus.QUEUED,
        SubagentStatus.QUEUED,
    ]

    factory.gates["task_1"].set()
    await wait_until_started(factory, 5)
    assert factory.started[-1] == "task_4"
    factory.gates["task_0"].set()
    await wait_until_started(factory, 6)
    assert factory.started[-1] == "task_5"
    for gate in factory.gates.values():
        gate.set()
    results = await asyncio.gather(*(coord.wait(record.subagent_id) for record in records))
    assert [result.task_name for result in results] == [spec.task_name for spec in specs]
    assert factory.peak == 4


async def test_default_total_limit_rejects_ninth_task_without_partial_creation(
    tmp_path: Path,
) -> None:
    coord, _, _ = coordinator(tmp_path, config=SubagentConfig())
    with pytest.raises(SubagentCoordinatorError) as error:
        await coord.spawn(tuple(task(f"task_{index}") for index in range(9)))
    assert error.value.category == "capacity_exceeded"
    assert coord.list() == ()


async def test_network_read_task_is_rejected_before_creation(tmp_path: Path) -> None:
    coord, factory, _ = coordinator(tmp_path)
    network_task = replace(task("weather"), requires_network=True)
    with pytest.raises(SubagentCoordinatorError) as error:
        await coord.spawn((network_task,))
    assert error.value.category == "capability_unavailable"
    assert coord.list() == ()
    assert factory.started == []


async def test_cancel_queued_task_does_not_affect_running_sibling(tmp_path: Path) -> None:
    coord, factory, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=3, max_concurrent=1),
    )
    first, second = await coord.spawn((task("first"), task("second")))
    await wait_until_started(factory, 1)
    cancelled = await coord.cancel(second.subagent_id)
    assert cancelled.status is SubagentStatus.CANCELLED
    factory.gates["first"].set()
    assert (await coord.wait(first.subagent_id)).status is SubagentStatus.COMPLETED
    assert factory.started == ["first"]


async def test_recovery_marks_interrupted_records_without_starting_tasks(tmp_path: Path) -> None:
    coord, factory, bus = coordinator(tmp_path)
    del coord
    queued = SubagentRecord("queued", "parent", "run", 0, task("queued"))
    running = replace(
        SubagentRecord("running", "parent", "run", 1, task("running")),
        status=SubagentStatus.RUNNING,
    )
    for record in (queued, running):
        bus.session_store.append("subagent_record", subagent_record_to_dict(record), durable=True)
    recovered, _, _ = coordinator(tmp_path, factory=factory, bus=bus)
    records = await recovered.recover()
    assert [record.status for record in records] == [
        SubagentStatus.CANCELLED,
        SubagentStatus.CANCELLED,
    ]
    assert factory.started == []
