from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryKind(StrEnum):
    USER_PROFILE = "user_profile"
    PROJECT_KNOWLEDGE = "project_knowledge"
    EXPERIENCE = "experience"
    SOP = "sop"
    REFERENCE = "reference"


class MemoryActivation(StrEnum):
    ALWAYS = "always"
    SEARCH = "search"
    MANUAL = "manual"


def default_memory_activation(kind: MemoryKind) -> MemoryActivation:
    if kind is MemoryKind.USER_PROFILE:
        return MemoryActivation.ALWAYS
    if kind in {MemoryKind.EXPERIENCE, MemoryKind.SOP, MemoryKind.PROJECT_KNOWLEDGE}:
        return MemoryActivation.SEARCH
    return MemoryActivation.MANUAL


def default_memory_priority(kind: MemoryKind) -> int:
    return {
        MemoryKind.USER_PROFILE: 80,
        MemoryKind.PROJECT_KNOWLEDGE: 50,
        MemoryKind.EXPERIENCE: 50,
        MemoryKind.SOP: 70,
        MemoryKind.REFERENCE: 40,
    }[kind]


class MemoryScope(StrEnum):
    USER = "user"
    PROJECT = "project"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class MemorySource:
    session_id: str
    run_id: str
    event_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "event_ids": list(self.event_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MemorySource:
        return cls(
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            event_ids=tuple(str(item) for item in value.get("event_ids", ())),
        )


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    scope: MemoryScope
    title: str
    summary: str
    body: str
    activation: MemoryActivation
    priority: int
    project_id: str | None = None
    tags: tuple[str, ...] = ()
    source: MemorySource | None = None
    evidence: tuple[str, ...] = ()
    confidence: float = 0.5
    status: MemoryStatus = MemoryStatus.CANDIDATE
    version: int = 1
    supersedes: str | None = None
    conflicts_with: tuple[str, ...] = ()
    success_count: int = 0
    failure_count: int = 0
    last_verified_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        *,
        kind: MemoryKind,
        scope: MemoryScope,
        title: str,
        summary: str,
        body: str,
        project_id: str | None = None,
        tags: tuple[str, ...] = (),
        source: MemorySource | None = None,
        evidence: tuple[str, ...] = (),
        confidence: float = 0.5,
        activation: MemoryActivation | None = None,
        priority: int | None = None,
    ) -> MemoryRecord:
        return cls(
            memory_id=uuid4().hex,
            kind=kind,
            scope=scope,
            title=title.strip(),
            summary=summary.strip(),
            body=body.strip(),
            activation=activation or default_memory_activation(kind),
            priority=default_memory_priority(kind) if priority is None else priority,
            project_id=project_id,
            tags=tags,
            source=source,
            evidence=evidence,
            confidence=max(0.0, min(1.0, confidence)),
        )

    def transition(self, status: MemoryStatus) -> MemoryRecord:
        return replace(self, status=status, version=self.version + 1, updated_at=utc_now())

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind.value,
            "scope": self.scope.value,
            "project_id": self.project_id,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "activation": self.activation.value,
            "priority": self.priority,
            "tags": list(self.tags),
            "source": None if self.source is None else self.source.to_dict(),
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "status": self.status.value,
            "version": self.version,
            "supersedes": self.supersedes,
            "conflicts_with": list(self.conflicts_with),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_verified_at": (
                None if self.last_verified_at is None else self.last_verified_at.isoformat()
            ),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MemoryRecord:
        source = value.get("source")
        kind = MemoryKind(str(value["kind"]))
        return cls(
            memory_id=str(value["memory_id"]),
            kind=kind,
            scope=MemoryScope(str(value["scope"])),
            project_id=None if value.get("project_id") is None else str(value["project_id"]),
            title=str(value["title"]),
            summary=str(value["summary"]),
            body=str(value["body"]),
            activation=MemoryActivation(
                str(value.get("activation", default_memory_activation(kind).value))
            ),
            priority=int(value.get("priority", default_memory_priority(kind))),
            tags=tuple(str(item) for item in value.get("tags", ())),
            source=(
                MemorySource.from_dict(cast(dict[str, Any], source))
                if isinstance(source, dict)
                else None
            ),
            evidence=tuple(str(item) for item in value.get("evidence", ())),
            confidence=float(value.get("confidence", 0.5)),
            status=MemoryStatus(str(value.get("status", "candidate"))),
            version=int(value.get("version", 1)),
            supersedes=(None if value.get("supersedes") is None else str(value.get("supersedes"))),
            conflicts_with=tuple(str(item) for item in value.get("conflicts_with", ())),
            success_count=int(value.get("success_count", 0)),
            failure_count=int(value.get("failure_count", 0)),
            last_verified_at=(
                None
                if value.get("last_verified_at") is None
                else datetime.fromisoformat(str(value["last_verified_at"]))
            ),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
        )


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    record: MemoryRecord
    score: float
