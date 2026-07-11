from dataclasses import replace
from pathlib import Path

from windcode.config import AppConfig, PermissionMode
from windcode.domain.subagents import (
    SubagentRecord,
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
    subagent_record_to_dict,
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


def task() -> SubagentTaskSpec:
    return SubagentTaskSpec(
        "interrupted",
        SubagentRole.RESEARCHER,
        SubagentTaskKind.READ,
        "inspect",
        "context",
        "report",
        ("evidence",),
    )


async def test_recovery_is_idempotent_and_never_starts_a_model(tmp_path: Path) -> None:
    state = tmp_path / "state"
    session = SessionStore.create(state / "sessions", "parent")
    bus = EventBus(session, TraceStore("run", root=state / "traces"))
    interrupted = replace(
        SubagentRecord("child", "parent", "run", 0, task()),
        status=SubagentStatus.RUNNING,
    )
    session.append("subagent_record", subagent_record_to_dict(interrupted), durable=True)

    def no_model(_model: str | None) -> tuple[ModelTarget, ...]:
        raise AssertionError("recovery must not resolve or call a model")

    config = AppConfig()
    factory = ChildRuntimeFactory(
        config=config,
        state_root=state,
        parent_tools=create_builtin_registry(),
        model_chain=no_model,
    )
    coord = SubagentCoordinator(
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
    first = await coord.recover()
    second = await coord.recover()
    assert first[0].status is SubagentStatus.CANCELLED
    assert second[0].status is SubagentStatus.CANCELLED
    assert second[0].error_message == "interrupted before recovery"
