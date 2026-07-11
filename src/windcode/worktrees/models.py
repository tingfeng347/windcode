from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class GitErrorCategory(StrEnum):
    NOT_REPOSITORY = "not_repository"
    DIRTY_WORKSPACE = "dirty_workspace"
    DETACHED_HEAD = "detached_head"
    WORKTREE_UNAVAILABLE = "worktree_unavailable"
    COMMAND_FAILED = "command_failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    INVALID_PATH = "invalid_path"


class WorktreeError(RuntimeError):
    def __init__(self, category: GitErrorCategory, message: str) -> None:
        self.category = category
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GitBaseline:
    repository: Path
    branch: str
    commit: str


@dataclass(frozen=True, slots=True)
class WorktreeLease:
    subagent_id: str
    path: Path
    branch: str
    base_commit: str


@dataclass(frozen=True, slots=True)
class WorktreeResult:
    clean: bool
    commit: str | None
    changed_files: tuple[str, ...]
    diff_stat: str


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    integrated: bool
    parent_commit_before: str
    parent_commit_after: str
    conflict_files: tuple[str, ...] = ()
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed: bool
    retained_path: Path | None = None
    reason: str | None = None
