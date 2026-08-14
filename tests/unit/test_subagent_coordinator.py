from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from windcode.config import PermissionMode, SubagentConfig
from windcode.domain import subagents as domain_subagents
from windcode.domain.events import RunResult
from windcode.domain.subagents import (
    CollaborationMode,
    CollaborationParticipant,
    CollaborationRequest,
    CollaborationResult,
    SubagentRecord,
    SubagentResult,
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
    subagent_record_to_dict,
)
from windcode.domain.tools import ToolContext
from windcode.observability import TraceStore
from windcode.runtime.control import RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.loop import AgentLoop
from windcode.runtime.subagents.collaboration import BoundSubagentCollaboration
from windcode.runtime.subagents.coordinator import (
    ChildRunPreparer,
    ChildRunProfile,
    SubagentCoordinator,
    SubagentCoordinatorError,
)
from windcode.runtime.subagents.runtime import ChildRuntime
from windcode.runtime.subagents.teamwork import run_collaboration
from windcode.runtime.subagents.verification import VerificationRunner
from windcode.sessions import SessionStore
from windcode.tools import ToolRegistry
from windcode.tools.subagents import SubagentOperations, register_subagent_tools
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


class FakePolicy:
    def __init__(self, factory: FakeFactory) -> None:
        self.factory = factory

    def set_mode(self, mode: PermissionMode) -> None:
        self.factory.permission_updates.append(mode)


class FakeScheduler:
    def __init__(self, factory: FakeFactory) -> None:
        self.policy = FakePolicy(factory)


class FakeLoop:
    def __init__(self, factory: FakeFactory, name: str, event_bus: EventBus) -> None:
        self.factory = factory
        self.name = name
        self.event_bus = event_bus
        self.scheduler = FakeScheduler(factory)
        self.system_prompt = "权限模式: default."

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
        self.permission_updates: list[PermissionMode] = []

    def __call__(self, profile: ChildRunProfile) -> ChildRuntime:
        record = profile.record
        workspace = profile.workspace
        name = record.spec.task_name
        self.gates.setdefault(name, asyncio.Event())
        session_id = f"child-{name}"
        child_record = replace(record, child_session_id=session_id)
        session = SessionStore.create(self.tmp_path / "child-sessions", session_id)
        bus = EventBus(session, TraceStore(session_id, root=self.tmp_path / "child-traces"))
        loop = cast(AgentLoop, FakeLoop(self, name, bus))
        return ChildRuntime(child_record, RunControl(), bus, loop, workspace, name)


class FakeSubagentOperations:
    async def spawn(self, specs: tuple[SubagentTaskSpec, ...]) -> tuple[SubagentRecord, ...]:
        del specs
        return ()

    def list(self) -> tuple[SubagentRecord, ...]:
        return ()

    async def wait(self, subagent_id: str) -> SubagentResult:
        raise AssertionError(f"unexpected wait: {subagent_id}")

    async def cancel(self, subagent_id: str) -> SubagentRecord:
        raise AssertionError(f"unexpected cancel: {subagent_id}")

    async def integrate(
        self,
        subagent_id: str,
        verification_commands: tuple[str, ...] = (),
    ) -> SubagentResult:
        raise AssertionError(f"unexpected integrate: {subagent_id}, {verification_commands}")

    async def collaborate(self, request: CollaborationRequest) -> CollaborationResult:
        return CollaborationResult("fake", request.request, request.mode, "completed")


class CoordinatingLoop:
    def __init__(
        self,
        record: SubagentRecord,
        collaboration: BoundSubagentCollaboration,
        event_bus: EventBus,
        fail: bool = False,
    ) -> None:
        self.record = record
        self.collaboration = collaboration
        self.event_bus = event_bus
        self.fail = fail

    async def run(self, prompt: str, workspace: Path) -> RunResult:
        del prompt, workspace
        try:
            if self.fail:
                return RunResult(status="failed", final_text="participant failed")
            if self.record.spec.coordination_id is not None:
                for round_index in range(self.record.spec.coordination_rounds + 1):
                    await self.collaboration.exchange_round(
                        round_index,
                        f"{self.record.spec.coordination_participant} contribution {round_index}",
                        1,
                    )
            return RunResult(
                status="completed", final_text=f"completed {self.record.spec.task_name}"
            )
        finally:
            await self.event_bus.close()


class CoordinatingFactory(FakeFactory):
    def __init__(self, tmp_path: Path, *, fail_participant: str | None = None) -> None:
        super().__init__(tmp_path)
        self.fail_participant = fail_participant

    def __call__(self, profile: ChildRunProfile) -> ChildRuntime:
        runtime = super().__call__(profile)
        record = profile.record
        collaboration = cast(BoundSubagentCollaboration, profile.collaboration)
        runtime.loop = cast(
            AgentLoop,
            CoordinatingLoop(
                runtime.record,
                collaboration,
                runtime.event_bus,
                fail=(
                    self.fail_participant is not None
                    and record.spec.coordination_participant == self.fail_participant
                ),
            ),
        )
        return runtime


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
        prepare_child=cast(ChildRunPreparer, child_factory),
        worktrees=WorktreeManager(worktrees_root=tmp_path / "worktrees"),
        verification=VerificationRunner(),
    )
    return instance, child_factory, bus


async def wait_until_started(factory: FakeFactory, count: int) -> None:
    while len(factory.started) < count:
        factory.started_event.clear()
        await factory.started_event.wait()


async def test_subagent_tools_accept_consumer_side_fake(tmp_path: Path) -> None:
    operations: SubagentOperations = FakeSubagentOperations()
    registry = ToolRegistry()
    register_subagent_tools(registry=registry, coordinator=operations)

    result = await registry.execute(
        "list_subagents",
        ToolContext(tmp_path, "run", lambda: False),
        {},
    )

    assert result.data == {"subagents": []}
    assert registry.names() == (
        "spawn_subagents",
        "list_subagents",
        "wait_subagents",
        "cancel_subagent",
        "integrate_subagent",
        "collaborate_subagents",
    )


def test_subagent_error_keeps_runtime_import_identity() -> None:
    assert SubagentCoordinatorError is domain_subagents.SubagentCoordinatorError


async def test_reattach_rebinds_running_permissions_and_approval_identity(
    tmp_path: Path,
) -> None:
    coord, factory, bus = coordinator(tmp_path)
    (record,) = await coord.spawn((task("child"),))
    await wait_until_started(factory, 1)
    replacement_factory = FakeFactory(tmp_path)
    replacement_verification = VerificationRunner()

    coord.reattach(
        event_bus=bus,
        event_observer=None,
        parent_run_id="next-run",
        permission_mode=PermissionMode.PLAN,
        prepare_child=cast(ChildRunPreparer, replacement_factory),
        verification=replacement_verification,
    )

    assert factory.permission_updates == [PermissionMode.PLAN]
    assert coord.approvals.parent_run_id == "next-run"
    assert coord.verification is replacement_verification
    factory.gates["child"].set()
    await coord.wait(record.subagent_id)


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


async def test_spawn_returns_only_after_scheduled_children_leave_queued_state(
    tmp_path: Path,
) -> None:
    coord, factory, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=3, max_concurrent=3),
    )
    records = await coord.spawn((task("first"), task("second"), task("third")))

    assert factory.started == ["first", "second", "third"]
    assert all(record.status is SubagentStatus.RUNNING for record in records)

    for gate in factory.gates.values():
        gate.set()
    await asyncio.gather(*(coord.wait(record.subagent_id) for record in records))


async def test_factory_failure_is_reported_instead_of_remaining_queued(tmp_path: Path) -> None:
    class FailingFactory(FakeFactory):
        def __call__(self, profile: ChildRunProfile) -> ChildRuntime:
            del profile
            raise ValueError("selected MCP tool is unavailable to child")

    factory = FailingFactory(tmp_path)
    coord, _, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=1, max_concurrent=1),
        factory=factory,
    )
    (record,) = await coord.spawn((task("broken"),))
    result = await coord.wait(record.subagent_id)

    assert result.status is SubagentStatus.FAILED
    assert result.error_category == "ValueError"
    assert coord.list()[0].status is SubagentStatus.FAILED


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


async def test_spawn_reuses_task_name_after_terminal(tmp_path: Path) -> None:
    coord, factory, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=4, max_concurrent=2),
    )
    (first,) = await coord.spawn((task("reuse"),))
    factory.gates["reuse"].set()
    await coord.wait(first.subagent_id)
    assert coord.list()[0].status is SubagentStatus.COMPLETED

    # A terminal record must not block a new task with the same name.
    (second,) = await coord.spawn((task("reuse"),))
    assert second.spec.task_name == "reuse"
    assert second.subagent_id != first.subagent_id


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


async def test_siblings_exchange_fifo_messages_by_name_or_id(tmp_path: Path) -> None:
    coord, factory, _ = coordinator(tmp_path)
    first, second = await coord.spawn((task("first"), task("second")))

    one = await coord.send_message(first.subagent_id, "second", "first message")
    two = await coord.send_message(first.subagent_id, second.subagent_id, "second message")
    received = await coord.receive_messages(second.subagent_id, max_messages=20)

    assert [message.message_id for message in received] == [one.message_id, two.message_id]
    assert [message.content for message in received] == ["first message", "second message"]
    assert all(message.delivered_at is not None for message in received)

    for gate in factory.gates.values():
        gate.set()
    await asyncio.gather(coord.wait(first.subagent_id), coord.wait(second.subagent_id))


async def test_messages_support_queued_recipient_and_reject_invalid_targets(
    tmp_path: Path,
) -> None:
    coord, factory, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=2, max_concurrent=1),
    )
    first, second = await coord.spawn((task("first"), task("second")))

    await coord.send_message(first.subagent_id, "second", "queued hello")
    received = await coord.receive_messages(second.subagent_id, max_messages=1)
    assert received[0].content == "queued hello"

    for target, category in (
        (first.subagent_id, "self_recipient"),
        ("missing", "unknown_recipient"),
    ):
        with pytest.raises(SubagentCoordinatorError) as error:
            await coord.send_message(first.subagent_id, target, "hello")
        assert error.value.category == category

    await coord.cancel(second.subagent_id)
    with pytest.raises(SubagentCoordinatorError) as error:
        await coord.send_message(first.subagent_id, second.subagent_id, "too late")
    assert error.value.category == "recipient_terminal"
    factory.gates["first"].set()
    await coord.wait(first.subagent_id)


async def test_wait_for_messages_times_out_and_shutdown_wakes_waiter(tmp_path: Path) -> None:
    coord, factory, _ = coordinator(tmp_path)
    first, second = await coord.spawn((task("first"), task("second")))

    assert (
        await coord.receive_messages(second.subagent_id, max_messages=20, timeout_seconds=0.01)
        == ()
    )
    waiting = asyncio.create_task(
        coord.receive_messages(second.subagent_id, max_messages=20, timeout_seconds=10)
    )
    await asyncio.sleep(0)
    await coord.shutdown("test shutdown")
    assert await waiting == ()
    assert (await coord.wait(first.subagent_id)).status is SubagentStatus.CANCELLED
    assert (await coord.wait(second.subagent_id)).status is SubagentStatus.CANCELLED
    for gate in factory.gates.values():
        gate.set()


async def test_multiple_participants_exchange_synchronized_rounds(tmp_path: Path) -> None:
    coord, factory, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=4, max_concurrent=3),
    )
    participants = ("research", "implementation", "review")
    await coord.register_coordination_session(
        "team",
        CollaborationMode.HYBRID,
        participants,
        rounds=1,
    )
    specs = tuple(
        replace(
            task(name),
            peer_collaboration=False,
            coordination_id="team",
            coordination_participant=name,
            coordination_rounds=1,
        )
        for name in participants
    )
    records = await coord.spawn(specs)

    opening = await asyncio.gather(
        *(
            coord.exchange_coordination_round(
                record.subagent_id,
                0,
                f"opening from {name}",
                1,
            )
            for record, name in zip(records, participants, strict=True)
        )
    )
    assert all(
        [item.participant_name for item in result] == list(participants) for result in opening
    )

    updates = await asyncio.gather(
        *(
            coord.exchange_coordination_round(
                record.subagent_id,
                1,
                f"integrated update from {name}",
                1,
            )
            for record, name in zip(records, participants, strict=True)
        )
    )
    assert all(len(result) == 3 for result in updates)
    transcript = coord.coordination_contributions("team")
    assert len(transcript) == 6
    assert [item.round_index for item in transcript] == [0, 0, 0, 1, 1, 1]

    for gate in factory.gates.values():
        gate.set()
    await asyncio.gather(*(coord.wait(record.subagent_id) for record in records))


async def test_teamwork_keeps_multiple_participants_alive_across_rounds(
    tmp_path: Path,
) -> None:
    factory = CoordinatingFactory(tmp_path)
    coord, _, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=4, max_concurrent=3),
        factory=factory,
    )
    result = await run_collaboration(
        coord,
        CollaborationRequest(
            request="分工调研三个模块,再互相评审并统一方案",
            context="repository",
            participants=(
                CollaborationParticipant("research", "investigate requirements"),
                CollaborationParticipant("design", "design interfaces"),
                CollaborationParticipant("review", "review risks"),
            ),
            rounds=1,
        ),
    )

    assert result.status == "completed", result
    assert result.mode is CollaborationMode.HYBRID
    assert len(result.participant_results) == 3
    assert len(result.contributions) == 6
    for participant in ("research", "design", "review"):
        ids = {
            item.subagent_id
            for item in result.contributions
            if item.participant_name == participant
        }
        assert len(ids) == 1


async def test_failed_participant_immediately_cancels_coordination_siblings(
    tmp_path: Path,
) -> None:
    factory = CoordinatingFactory(tmp_path, fail_participant="review")
    coord, _, _ = coordinator(
        tmp_path,
        config=SubagentConfig(max_tasks=4, max_concurrent=3),
        factory=factory,
    )
    async with asyncio.timeout(1):
        result = await run_collaboration(
            coord,
            CollaborationRequest(
                request="parallel implementation with peer review",
                context="repository",
                participants=(
                    CollaborationParticipant("research", "investigate requirements"),
                    CollaborationParticipant("design", "design interfaces"),
                    CollaborationParticipant("review", "review risks"),
                ),
                rounds=1,
            ),
        )
    assert result.status == "failed"
    assert result.error_category == "collaboration_incomplete"
    assert {item.status for item in result.participant_results} == {
        SubagentStatus.CANCELLED,
        SubagentStatus.FAILED,
    }


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
