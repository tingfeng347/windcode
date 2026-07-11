from __future__ import annotations

import json
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from windcode.config import AppConfig, PermissionMode
from windcode.domain.messages import Role, TextBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
)
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


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments), cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Windcode Tests")
    git(repo, "config", "user.email", "windcode@example.test")
    (repo / "example.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "example.txt")
    git(repo, "commit", "-m", "initial")
    return repo


class CommittingTransport:
    name = "committing"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        last = request.messages[-1]
        if last.role is Role.USER:
            block = last.content[0]
            assert isinstance(block, TextBlock)
            task_match = re.search(r"^Task: ([a-z0-9_]+)$", block.text, re.MULTILINE)
            assert task_match is not None
            task_name = task_match.group(1)
            if "change the shared base line" in block.text:
                command = (
                    "printf 'child\\n' > example.txt && git add example.txt && "
                    f"git commit -m '{task_name}'"
                )
            else:
                command = (
                    f"printf '{task_name}\\n' > {task_name}.txt && git add {task_name}.txt && "
                    f"git commit -m '{task_name}'"
                )
            yield ToolCallDelta("commit", "shell", json.dumps({"command": command}))
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("implemented and committed")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


def write_task(name: str, goal: str = "add an independent file") -> SubagentTaskSpec:
    return SubagentTaskSpec(
        name,
        SubagentRole.WORKER,
        SubagentTaskKind.WRITE,
        goal,
        "Use the assigned Git Worktree and commit the result.",
        "A clean commit.",
        ("Commit the change.",),
    )


def coordinator(tmp_path: Path, repo: Path) -> SubagentCoordinator:
    state = tmp_path / "state"
    parent_session = SessionStore.create(state / "sessions", "parent")
    parent_bus = EventBus(
        parent_session,
        TraceStore("parent-run", root=state / "traces"),
    )
    transport = CommittingTransport()
    target = ModelTarget("committing", "model", transport)
    app_config = AppConfig()
    factory = ChildRuntimeFactory(
        config=app_config,
        state_root=state,
        parent_tools=create_builtin_registry(),
        model_chain=lambda _model: (target,),
    )
    return SubagentCoordinator(
        parent_session_id="parent",
        parent_run_id="parent-run",
        workspace=repo,
        permission_mode=PermissionMode.FULL_ACCESS,
        config=app_config.subagents,
        event_bus=parent_bus,
        factory=factory,
        worktrees=WorktreeManager(worktrees_root=tmp_path / "worktrees"),
        verification=VerificationRunner(),
    )


async def test_write_task_integrates_verifies_and_cleans(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    coord = coordinator(tmp_path, repo)
    (record,) = await coord.spawn((write_task("add_child"),))
    completed = await coord.wait(record.subagent_id)
    assert completed.status is SubagentStatus.COMPLETED
    assert completed.commit is not None
    worktree = coord.list()[0].worktree_path
    assert worktree is not None and worktree.exists()

    integrated = await coord.integrate(
        record.subagent_id,
        ("test -f add_child.txt",),
    )
    assert integrated.status is SubagentStatus.INTEGRATED
    assert (repo / "add_child.txt").read_text(encoding="utf-8") == "add_child\n"
    assert not worktree.exists()


async def test_parent_verification_failure_preserves_integrated_evidence(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    coord = coordinator(tmp_path, repo)
    (record,) = await coord.spawn((write_task("bad_verification"),))
    await coord.wait(record.subagent_id)
    worktree = coord.list()[0].worktree_path
    assert worktree is not None

    result = await coord.integrate(record.subagent_id, ("false",))
    assert result.status is SubagentStatus.INTEGRATION_FAILED
    assert (repo / "bad_verification.txt").exists()
    assert worktree.exists()


async def test_integration_conflict_aborts_and_preserves_child_worktree(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    coord = coordinator(tmp_path, repo)
    (record,) = await coord.spawn((write_task("conflicting", "change the shared base line"),))
    await coord.wait(record.subagent_id)
    worktree = coord.list()[0].worktree_path
    assert worktree is not None
    (repo / "example.txt").write_text("parent\n", encoding="utf-8")
    git(repo, "add", "example.txt")
    git(repo, "commit", "-m", "parent change")
    before = git(repo, "rev-parse", "HEAD")

    result = await coord.integrate(record.subagent_id)
    assert result.status is SubagentStatus.CONFLICT
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "status", "--porcelain") == ""
    assert worktree.exists()
