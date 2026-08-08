from __future__ import annotations

import json
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

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
from windcode.runtime.subagents.coordinator import (
    SubagentCoordinator,
    SubagentCoordinatorError,
)
from windcode.runtime.subagents.factory import ChildRuntimeFactory
from windcode.runtime.subagents.verification import VerificationRunner
from windcode.sessions import SessionStore
from windcode.tools import create_builtin_registry
from windcode.worktrees import (
    GitBaseline,
    GitErrorCategory,
    WorktreeError,
    WorktreeManager,
)


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments), cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def git_without_check(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("git", *arguments), cwd=cwd, text=True, capture_output=True, check=False)


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


def read_task(name: str) -> SubagentTaskSpec:
    return SubagentTaskSpec(
        name,
        SubagentRole.RESEARCHER,
        SubagentTaskKind.READ,
        "inspect the parent workspace",
        "Read only and report findings.",
        "A concise report.",
        ("Do not modify files.",),
    )


def coordinator(
    tmp_path: Path,
    repo: Path,
    *,
    worktrees: WorktreeManager | None = None,
) -> SubagentCoordinator:
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
        worktrees=worktrees or WorktreeManager(worktrees_root=tmp_path / "worktrees"),
        verification=VerificationRunner(),
    )


class UnavailableWorktreeManager(WorktreeManager):
    async def validate_parent(
        self,
        workspace: Path,
        *,
        require_clean: bool = True,
    ) -> GitBaseline:
        del workspace, require_clean
        raise WorktreeError(GitErrorCategory.WORKTREE_UNAVAILABLE, "Git Worktree is unavailable")


async def assert_write_rejected_but_read_runs(coord: SubagentCoordinator) -> None:
    with pytest.raises(SubagentCoordinatorError) as error:
        await coord.spawn((read_task("inspect_parent"), write_task("isolated_child")))
    assert error.value.category == "write_workspace_blocked"
    assert coord.list() == ()

    (record,) = await coord.spawn((read_task("inspect_parent"),))
    completed = await coord.wait(record.subagent_id)
    assert completed.status is SubagentStatus.COMPLETED
    assert completed.commit is None
    assert coord.list()[0].worktree_path is None


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


@pytest.mark.parametrize("dirty_state", ("tracked", "untracked", "staged", "conflicted"))
async def test_dirty_parent_rejects_write_batch_but_read_task_still_runs(
    tmp_path: Path,
    dirty_state: str,
) -> None:
    repo = repository(tmp_path)
    if dirty_state == "tracked":
        (repo / "example.txt").write_text("modified\n", encoding="utf-8")
    elif dirty_state == "untracked":
        (repo / "parent-only.txt").write_text("preserve me\n", encoding="utf-8")
    elif dirty_state == "staged":
        (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        git(repo, "add", "staged.txt")
    else:
        git(repo, "checkout", "-b", "conflicting-parent")
        (repo / "example.txt").write_text("other\n", encoding="utf-8")
        git(repo, "commit", "-am", "other change")
        git(repo, "checkout", "main")
        (repo / "example.txt").write_text("parent\n", encoding="utf-8")
        git(repo, "commit", "-am", "parent change")
        merge = git_without_check(repo, "merge", "conflicting-parent")
        assert merge.returncode != 0
    coord = coordinator(tmp_path, repo)

    await assert_write_rejected_but_read_runs(coord)

    assert git(repo, "status", "--porcelain")


async def test_non_git_parent_rejects_write_batch_but_read_task_still_runs(tmp_path: Path) -> None:
    workspace = tmp_path / "plain"
    workspace.mkdir()
    coord = coordinator(tmp_path, workspace)

    await assert_write_rejected_but_read_runs(coord)


async def test_missing_worktree_support_rejects_write_but_read_task_still_runs(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    coord = coordinator(tmp_path, repo, worktrees=UnavailableWorktreeManager())

    await assert_write_rejected_but_read_runs(coord)


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
