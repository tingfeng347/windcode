from __future__ import annotations

import asyncio
import re
from pathlib import Path

from windcode.domain.subagents import SubagentRecord
from windcode.worktrees.git import GitRunner
from windcode.worktrees.models import (
    CleanupResult,
    GitBaseline,
    GitErrorCategory,
    IntegrationResult,
    WorktreeError,
    WorktreeLease,
    WorktreeResult,
)

_SAFE_COMPONENT = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_component(value: str) -> str:
    cleaned = _SAFE_COMPONENT.sub("-", value).strip(".-")
    return cleaned[:48] or "task"


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _exists(path: Path) -> bool:
    return path.exists()


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


class WorktreeManager:
    def __init__(
        self,
        *,
        runner: GitRunner | None = None,
        worktrees_root: Path | None = None,
    ) -> None:
        self.runner = runner or GitRunner()
        self.worktrees_root = _resolve(worktrees_root) if worktrees_root else None
        self._lock = asyncio.Lock()

    def _effective_root(self, repository: Path) -> Path:
        default = repository.parent / f".{repository.name}-windcode-worktrees"
        if self.worktrees_root is None:
            return default
        # A project-local state root such as `.windcode` is valid for metadata,
        # sessions, and traces, but Git forbids linked worktree checkouts inside
        # the parent repository. Relocate only the checkout directory.
        if self.worktrees_root.is_relative_to(repository):
            return default
        return self.worktrees_root

    async def validate_parent(
        self,
        workspace: Path,
        *,
        require_clean: bool = True,
    ) -> GitBaseline:
        workspace = _resolve(workspace)
        try:
            repository_result = await self.runner.run(
                ("rev-parse", "--show-toplevel"), cwd=workspace
            )
        except FileNotFoundError as exc:
            raise WorktreeError(
                GitErrorCategory.WORKTREE_UNAVAILABLE, "Git is unavailable"
            ) from exc
        repository = _resolve(Path(repository_result.stdout.strip()))
        branch_result = await self.runner.run(
            ("symbolic-ref", "--quiet", "--short", "HEAD"), cwd=repository, check=False
        )
        if branch_result.returncode != 0 or not branch_result.stdout.strip():
            raise WorktreeError(
                GitErrorCategory.DETACHED_HEAD,
                "write subagents require the parent workspace to be on a branch",
            )
        commit = (await self.runner.run(("rev-parse", "HEAD"), cwd=repository)).stdout.strip()
        await self.runner.run(("worktree", "list", "--porcelain"), cwd=repository)
        if require_clean:
            status = (
                await self.runner.run(
                    ("status", "--porcelain=v1", "--untracked-files=all"), cwd=repository
                )
            ).stdout
            if status.strip():
                details = ", ".join(line.strip() for line in status.splitlines()[:5])
                raise WorktreeError(
                    GitErrorCategory.DIRTY_WORKSPACE,
                    f"parent workspace is not clean: {details}",
                )
        return GitBaseline(
            repository=repository, branch=branch_result.stdout.strip(), commit=commit
        )

    async def create(
        self,
        subagent_id: str,
        task_name: str,
        baseline: GitBaseline,
        *,
        parent_run_id: str = "run",
    ) -> WorktreeLease:
        safe_run = _safe_component(parent_run_id)
        safe_task = _safe_component(task_name)
        safe_id = _safe_component(subagent_id)
        branch = f"windcode/subagents/{safe_run}/{safe_task}-{safe_id[:12]}"
        root = self._effective_root(baseline.repository)
        path = _resolve(root / f"{safe_task}-{safe_id[:12]}")
        if path.is_relative_to(baseline.repository):
            raise WorktreeError(
                GitErrorCategory.INVALID_PATH,
                "subagent Worktree must be outside the parent repository",
            )
        async with self._lock:
            _mkdir(root)
            if _exists(path):
                raise WorktreeError(
                    GitErrorCategory.INVALID_PATH, f"Worktree path already exists: {path}"
                )
            await self.runner.run(
                ("worktree", "add", "-b", branch, str(path), baseline.commit),
                cwd=baseline.repository,
            )
            lease = WorktreeLease(subagent_id, path, branch, baseline.commit)
            try:
                await self._validate_lease(lease)
            except BaseException:
                await self.runner.run(
                    ("worktree", "remove", "--force", str(path)),
                    cwd=baseline.repository,
                    check=False,
                )
                await self.runner.run(
                    ("branch", "-D", branch), cwd=baseline.repository, check=False
                )
                raise
            return lease

    async def _validate_lease(self, lease: WorktreeLease) -> Path:
        if not _exists(lease.path):
            raise WorktreeError(
                GitErrorCategory.INVALID_PATH, f"Worktree path does not exist: {lease.path}"
            )
        root = _resolve(
            Path(
                (
                    await self.runner.run(("rev-parse", "--show-toplevel"), cwd=lease.path)
                ).stdout.strip()
            )
        )
        if root != _resolve(lease.path):
            raise WorktreeError(GitErrorCategory.INVALID_PATH, "Worktree path ownership mismatch")
        branch = (
            await self.runner.run(("branch", "--show-current"), cwd=lease.path)
        ).stdout.strip()
        if branch != lease.branch:
            raise WorktreeError(GitErrorCategory.INVALID_PATH, "Worktree branch mismatch")
        return root

    async def inspect(self, lease: WorktreeLease) -> WorktreeResult:
        await self._validate_lease(lease)
        status = (
            await self.runner.run(
                ("status", "--porcelain=v1", "--untracked-files=all"), cwd=lease.path
            )
        ).stdout
        head = (await self.runner.run(("rev-parse", "HEAD"), cwd=lease.path)).stdout.strip()
        commit = head if head != lease.base_commit else None
        changed_files: tuple[str, ...] = ()
        diff_stat = ""
        if commit is not None:
            names = (
                await self.runner.run(
                    ("diff", "--name-only", f"{lease.base_commit}..{commit}"), cwd=lease.path
                )
            ).stdout
            changed_files = tuple(line for line in names.splitlines() if line)
            diff_stat = (
                await self.runner.run(
                    ("diff", "--stat", f"{lease.base_commit}..{commit}"), cwd=lease.path
                )
            ).stdout.strip()
        return WorktreeResult(not status.strip(), commit, changed_files, diff_stat)

    async def integrate(self, lease: WorktreeLease, parent_workspace: Path) -> IntegrationResult:
        worktree = await self.inspect(lease)
        if not worktree.clean or worktree.commit is None:
            raise WorktreeError(
                GitErrorCategory.DIRTY_WORKSPACE,
                "only a clean Worktree with a new commit can be integrated",
            )
        async with self._lock:
            baseline = await self.validate_parent(parent_workspace)
            before = baseline.commit
            cherry_pick = await self.runner.run(
                ("cherry-pick", worktree.commit), cwd=baseline.repository, check=False
            )
            if cherry_pick.returncode == 0:
                after = (
                    await self.runner.run(("rev-parse", "HEAD"), cwd=baseline.repository)
                ).stdout.strip()
                return IntegrationResult(True, before, after)

            conflicts = (
                await self.runner.run(
                    ("diff", "--name-only", "--diff-filter=U"),
                    cwd=baseline.repository,
                    check=False,
                )
            ).stdout
            abort = await self.runner.run(
                ("cherry-pick", "--abort"), cwd=baseline.repository, check=False
            )
            restored = (
                await self.runner.run(("rev-parse", "HEAD"), cwd=baseline.repository)
            ).stdout.strip()
            if abort.returncode != 0 or restored != before:
                raise WorktreeError(
                    GitErrorCategory.COMMAND_FAILED,
                    "failed to abort conflicting integration and restore the parent HEAD",
                )
            return IntegrationResult(
                False,
                before,
                restored,
                tuple(line for line in conflicts.splitlines() if line),
                cherry_pick.stderr.strip() or cherry_pick.stdout.strip(),
            )

    async def cleanup(
        self,
        lease: WorktreeLease,
        repository: Path,
        *,
        integrated: bool,
    ) -> CleanupResult:
        if not integrated:
            return CleanupResult(False, lease.path, "task has not been successfully integrated")
        try:
            result = await self.inspect(lease)
        except WorktreeError as exc:
            return CleanupResult(False, lease.path, str(exc))
        if not result.clean:
            return CleanupResult(False, lease.path, "Worktree contains uncommitted changes")
        async with self._lock:
            removed = await self.runner.run(
                ("worktree", "remove", str(lease.path)), cwd=repository, check=False
            )
            if removed.returncode != 0:
                return CleanupResult(False, lease.path, removed.stderr.strip() or "remove failed")
            branch = await self.runner.run(
                ("branch", "-d", lease.branch), cwd=repository, check=False
            )
            if branch.returncode != 0:
                return CleanupResult(False, None, branch.stderr.strip() or "branch cleanup failed")
            return CleanupResult(True)

    async def recover(self, record: SubagentRecord) -> WorktreeLease | None:
        if record.worktree_path is None or not _exists(record.worktree_path):
            return None
        if record.branch is None or record.base_commit is None:
            raise WorktreeError(
                GitErrorCategory.INVALID_PATH, "persisted Worktree record is incomplete"
            )
        lease = WorktreeLease(
            record.subagent_id,
            _resolve(record.worktree_path),
            record.branch,
            record.base_commit,
        )
        await self._validate_lease(lease)
        return lease
