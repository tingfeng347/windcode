from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from windcode.domain.models import Usage

_TASK_NAME = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class SubagentRole(StrEnum):
    RESEARCHER = "researcher"
    WORKER = "worker"
    VERIFIER = "verifier"


class SubagentTaskKind(StrEnum):
    READ = "read"
    WRITE = "write"


class SubagentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    INTEGRATION_FAILED = "integration_failed"
    INTEGRATED = "integrated"


TERMINAL_SUBAGENT_STATUSES = frozenset(
    {
        SubagentStatus.BLOCKED,
        SubagentStatus.FAILED,
        SubagentStatus.CANCELLED,
        SubagentStatus.CONFLICT,
        SubagentStatus.INTEGRATION_FAILED,
        SubagentStatus.INTEGRATED,
    }
)

_ALLOWED_TRANSITIONS: dict[SubagentStatus, frozenset[SubagentStatus]] = {
    SubagentStatus.QUEUED: frozenset({SubagentStatus.RUNNING, SubagentStatus.CANCELLED}),
    SubagentStatus.RUNNING: frozenset(
        {
            SubagentStatus.BLOCKED,
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
            SubagentStatus.CANCELLED,
        }
    ),
    SubagentStatus.COMPLETED: frozenset(
        {
            SubagentStatus.INTEGRATED,
            SubagentStatus.CONFLICT,
            SubagentStatus.INTEGRATION_FAILED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class SubagentTaskSpec:
    task_name: str
    role: SubagentRole
    kind: SubagentTaskKind
    goal: str
    context: str
    expected_output: str
    verification: tuple[str, ...]
    allowed_tools: frozenset[str] | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not _TASK_NAME.fullmatch(self.task_name):
            raise ValueError("task_name must contain lowercase letters, numbers, and underscores")
        for name, value in (
            ("goal", self.goal),
            ("context", self.context),
            ("expected_output", self.expected_output),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if not self.verification or any(not command.strip() for command in self.verification):
            raise ValueError("verification must contain at least one non-empty requirement")
        if self.kind is SubagentTaskKind.WRITE and self.role is not SubagentRole.WORKER:
            raise ValueError(f"role {self.role.value} does not allow write tasks")
        if self.allowed_tools is not None and any(not name for name in self.allowed_tools):
            raise ValueError("allowed_tools cannot contain empty names")


@dataclass(frozen=True, slots=True)
class SubagentRecord:
    subagent_id: str
    parent_session_id: str
    parent_run_id: str
    task_index: int
    spec: SubagentTaskSpec
    status: SubagentStatus = SubagentStatus.QUEUED
    child_session_id: str | None = None
    base_commit: str | None = None
    branch: str | None = None
    worktree_path: Path | None = None
    commit: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_category: str | None = None
    error_message: str | None = None


def transition_subagent(
    record: SubagentRecord,
    status: SubagentStatus,
    *,
    now: datetime | None = None,
    error_category: str | None = None,
    error_message: str | None = None,
) -> SubagentRecord:
    if status not in _ALLOWED_TRANSITIONS.get(record.status, frozenset()):
        raise ValueError(
            f"invalid subagent status transition: {record.status.value} -> {status.value}"
        )
    changed_at = now or datetime.now(UTC)
    return replace(
        record,
        status=status,
        started_at=changed_at if status is SubagentStatus.RUNNING else record.started_at,
        finished_at=changed_at
        if status not in {SubagentStatus.QUEUED, SubagentStatus.RUNNING}
        else None,
        error_category=error_category,
        error_message=error_message,
    )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    command: str
    exit_code: int | None
    output_summary: str
    passed: bool


@dataclass(frozen=True, slots=True)
class SubagentResult:
    subagent_id: str
    task_name: str
    status: SubagentStatus
    summary: str
    changed_files: tuple[str, ...] = ()
    commit: str | None = None
    verification: tuple[VerificationResult, ...] = ()
    usage: Usage = field(default_factory=Usage)
    error_category: str | None = None
    error_message: str | None = None


def sort_subagent_records(records: tuple[SubagentRecord, ...]) -> tuple[SubagentRecord, ...]:
    return tuple(sorted(records, key=lambda record: record.task_index))


def subagent_record_to_dict(record: SubagentRecord) -> dict[str, Any]:
    spec = record.spec
    return {
        "subagent_id": record.subagent_id,
        "parent_session_id": record.parent_session_id,
        "parent_run_id": record.parent_run_id,
        "child_session_id": record.child_session_id,
        "task_index": record.task_index,
        "spec": {
            "task_name": spec.task_name,
            "role": spec.role.value,
            "kind": spec.kind.value,
            "goal": spec.goal,
            "context": spec.context,
            "expected_output": spec.expected_output,
            "verification": list(spec.verification),
            "allowed_tools": None if spec.allowed_tools is None else sorted(spec.allowed_tools),
            "model": spec.model,
        },
        "status": record.status.value,
        "base_commit": record.base_commit,
        "branch": record.branch,
        "worktree_path": None if record.worktree_path is None else str(record.worktree_path),
        "commit": record.commit,
        "created_at": record.created_at.isoformat(),
        "started_at": None if record.started_at is None else record.started_at.isoformat(),
        "finished_at": None if record.finished_at is None else record.finished_at.isoformat(),
        "error_category": record.error_category,
        "error_message": record.error_message,
    }


def subagent_record_from_dict(value: Mapping[str, object]) -> SubagentRecord:
    raw_spec = value.get("spec")
    if not isinstance(raw_spec, Mapping):
        raise ValueError("subagent record spec must be an object")
    spec_values = cast(Mapping[str, object], raw_spec)
    verification_value = spec_values.get("verification", ())
    allowed_tools = spec_values.get("allowed_tools")
    if not isinstance(verification_value, (list, tuple)):
        raise ValueError("subagent verification must be a sequence")
    if allowed_tools is not None and not isinstance(allowed_tools, (list, tuple, set, frozenset)):
        raise ValueError("subagent allowed_tools must be a sequence")
    verification = cast(Sequence[object], verification_value)
    allowed_tool_values = None if allowed_tools is None else cast(Sequence[object], allowed_tools)
    spec = SubagentTaskSpec(
        task_name=str(spec_values["task_name"]),
        role=SubagentRole(str(spec_values["role"])),
        kind=SubagentTaskKind(str(spec_values["kind"])),
        goal=str(spec_values["goal"]),
        context=str(spec_values["context"]),
        expected_output=str(spec_values["expected_output"]),
        verification=tuple(str(item) for item in verification),
        allowed_tools=(
            None
            if allowed_tool_values is None
            else frozenset(str(item) for item in allowed_tool_values)
        ),
        model=None if spec_values.get("model") is None else str(spec_values.get("model")),
    )

    def optional_time(name: str) -> datetime | None:
        item = value.get(name)
        return None if item is None else datetime.fromisoformat(str(item))

    path = value.get("worktree_path")
    return SubagentRecord(
        subagent_id=str(value["subagent_id"]),
        parent_session_id=str(value["parent_session_id"]),
        parent_run_id=str(value["parent_run_id"]),
        child_session_id=(
            None if value.get("child_session_id") is None else str(value.get("child_session_id"))
        ),
        task_index=int(str(value["task_index"])),
        spec=spec,
        status=SubagentStatus(str(value["status"])),
        base_commit=None if value.get("base_commit") is None else str(value.get("base_commit")),
        branch=None if value.get("branch") is None else str(value.get("branch")),
        worktree_path=None if path is None else Path(str(path)),
        commit=None if value.get("commit") is None else str(value.get("commit")),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        started_at=optional_time("started_at"),
        finished_at=optional_time("finished_at"),
        error_category=(
            None if value.get("error_category") is None else str(value.get("error_category"))
        ),
        error_message=(
            None if value.get("error_message") is None else str(value.get("error_message"))
        ),
    )
