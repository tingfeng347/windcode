from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from windcode.domain.subagents import (
    SubagentRecord,
    SubagentRole,
    SubagentTaskKind,
    SubagentTaskSpec,
)
from windcode.worktrees import GitErrorCategory, GitRunner, WorktreeError, WorktreeManager


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments), cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def repository(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Windcode Tests")
    git(path, "config", "user.email", "windcode@example.test")
    (path / "example.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", "example.txt")
    git(path, "commit", "-m", "initial")
    return path


def manager(tmp_path: Path) -> WorktreeManager:
    return WorktreeManager(worktrees_root=tmp_path / "worktrees")


async def committed_lease(tmp_path: Path, *, subagent_id: str = "child"):
    repo = repository(tmp_path)
    worktrees = manager(tmp_path)
    baseline = await worktrees.validate_parent(repo)
    lease = await worktrees.create(subagent_id, "update_example", baseline)
    (lease.path / "example.txt").write_text("child\n", encoding="utf-8")
    git(lease.path, "add", "example.txt")
    git(lease.path, "commit", "-m", "child change")
    return repo, worktrees, baseline, lease


async def test_git_runner_success_and_failure(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    runner = GitRunner()
    result = await runner.run(("rev-parse", "HEAD"), cwd=repo)
    assert result.returncode == 0
    with pytest.raises(WorktreeError) as error:
        await runner.run(("not-a-command",), cwd=repo)
    assert error.value.category is GitErrorCategory.COMMAND_FAILED


async def test_git_runner_timeout(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    runner = GitRunner(timeout_seconds=0.01)
    with pytest.raises(WorktreeError) as error:
        await runner.run(("-c", "alias.pause=!sleep 1", "pause"), cwd=repo)
    assert error.value.category is GitErrorCategory.TIMEOUT


async def test_baseline_rejects_non_git_dirty_and_detached(tmp_path: Path) -> None:
    worktrees = manager(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeError) as error:
        await worktrees.validate_parent(plain)
    assert error.value.category is GitErrorCategory.NOT_REPOSITORY

    repo = repository(tmp_path / "dirty")
    (repo / "untracked.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(WorktreeError) as error:
        await worktrees.validate_parent(repo)
    assert error.value.category is GitErrorCategory.DIRTY_WORKSPACE

    dirty_baseline = await worktrees.validate_parent(repo, require_clean=False)
    lease = await worktrees.create("dirty-child", "isolated", dirty_baseline)
    assert lease.path.exists()
    assert (repo / "untracked.txt").read_text(encoding="utf-8") == "dirty"
    assert not (lease.path / "untracked.txt").exists()

    detached = repository(tmp_path / "detached")
    git(detached, "checkout", "--detach")
    with pytest.raises(WorktreeError) as error:
        await worktrees.validate_parent(detached)
    assert error.value.category is GitErrorCategory.DETACHED_HEAD


async def test_create_parallel_leases_keeps_parent_unchanged(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    worktrees = manager(tmp_path)
    baseline = await worktrees.validate_parent(repo)
    first, second = await asyncio.gather(
        worktrees.create("first", "first_task", baseline, parent_run_id="parent"),
        worktrees.create("second", "second_task", baseline, parent_run_id="parent"),
    )
    assert first.path != second.path
    assert first.branch != second.branch
    assert git(repo, "rev-parse", "HEAD") == baseline.commit
    assert git(repo, "status", "--porcelain") == ""


async def test_project_local_state_root_keeps_checkout_in_ignored_state_directory(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    (repo / ".gitignore").write_text(".windcode/worktrees/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore local Worktrees")
    worktrees = WorktreeManager(worktrees_root=repo / ".windcode" / "worktrees")
    baseline = await worktrees.validate_parent(repo)

    lease = await worktrees.create("child", "write_report", baseline)

    assert lease.path.is_relative_to(repo / ".windcode" / "worktrees")
    assert lease.path.parent == repo / ".windcode" / "worktrees"
    assert git(repo, "status", "--porcelain") == ""


async def test_project_local_worktree_root_must_be_git_ignored(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    worktrees = WorktreeManager(worktrees_root=repo / "nested-worktrees")
    baseline = await worktrees.validate_parent(repo)

    with pytest.raises(WorktreeError, match="must be ignored") as error:
        await worktrees.create("child", "write_report", baseline)

    assert error.value.category is GitErrorCategory.INVALID_PATH
    assert not (repo / "nested-worktrees").exists()


async def test_missing_project_worktree_root_falls_back_to_isolated_user_state(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    project_root = repo / ".windcode" / "worktrees"
    user_root = tmp_path / "user-state" / "worktrees"
    worktrees = WorktreeManager(
        worktrees_root=project_root,
        fallback_worktrees_root=user_root,
    )
    baseline = await worktrees.validate_parent(repo)

    lease = await worktrees.create("child", "write_report", baseline)

    assert lease.path.is_relative_to(user_root)
    assert lease.path.parent.parent == user_root
    assert lease.path.parent.name.startswith("repo-")
    assert not project_root.exists()


async def test_existing_project_worktree_root_wins_over_user_fallback(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    (repo / ".gitignore").write_text(".windcode/worktrees/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore local Worktrees")
    project_root = repo / ".windcode" / "worktrees"
    project_root.mkdir(parents=True)
    user_root = tmp_path / "user-state" / "worktrees"
    worktrees = WorktreeManager(
        worktrees_root=project_root,
        fallback_worktrees_root=user_root,
    )
    baseline = await worktrees.validate_parent(repo)

    lease = await worktrees.create("child", "write_report", baseline)

    assert lease.path.parent == project_root
    assert not user_root.exists()


async def test_inspect_and_cleanup_require_clean_integrated_lease(tmp_path: Path) -> None:
    repo, worktrees, _, lease = await committed_lease(tmp_path)
    inspected = await worktrees.inspect(lease)
    assert inspected.clean
    assert inspected.commit is not None
    assert inspected.changed_files == ("example.txt",)

    retained = await worktrees.cleanup(lease, repo, integrated=False)
    assert not retained.removed and retained.retained_path == lease.path

    integrated = await worktrees.integrate(lease, repo)
    assert integrated.integrated
    cleaned = await worktrees.cleanup(lease, repo, integrated=True)
    assert cleaned.removed
    assert not lease.path.exists()


async def test_cleanup_retains_uncommitted_changes(tmp_path: Path) -> None:
    repo, worktrees, _, lease = await committed_lease(tmp_path)
    (lease.path / "dirty.txt").write_text("dirty", encoding="utf-8")
    result = await worktrees.cleanup(lease, repo, integrated=True)
    assert not result.removed
    assert "uncommitted" in (result.reason or "")


async def test_integrate_aborts_conflict_and_restores_parent_head(tmp_path: Path) -> None:
    repo, worktrees, _, lease = await committed_lease(tmp_path)
    (repo / "example.txt").write_text("parent\n", encoding="utf-8")
    git(repo, "add", "example.txt")
    git(repo, "commit", "-m", "parent change")
    before = git(repo, "rev-parse", "HEAD")

    result = await worktrees.integrate(lease, repo)

    assert not result.integrated
    assert result.conflict_files == ("example.txt",)
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "status", "--porcelain") == ""


async def test_recover_validates_persisted_lease(tmp_path: Path) -> None:
    repo, worktrees, baseline, lease = await committed_lease(tmp_path)
    del repo
    task = SubagentTaskSpec(
        "update_example",
        SubagentRole.WORKER,
        SubagentTaskKind.WRITE,
        "update",
        "context",
        "commit",
        ("test",),
    )
    record = SubagentRecord(
        "child",
        "parent",
        "run",
        0,
        task,
        base_commit=baseline.commit,
        branch=lease.branch,
        worktree_path=lease.path,
    )
    assert await worktrees.recover(record) == lease
    missing = SubagentRecord(
        "missing",
        "parent",
        "run",
        1,
        task,
        base_commit=baseline.commit,
        branch="missing",
        worktree_path=tmp_path / "missing",
    )
    assert await worktrees.recover(missing) is None
