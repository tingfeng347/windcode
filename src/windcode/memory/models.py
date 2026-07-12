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
    REFERENCE = "reference"


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
    ) -> MemoryRecord:
        return cls(
            memory_id=uuid4().hex,
            kind=kind,
            scope=scope,
            title=title.strip(),
            summary=summary.strip(),
            body=body.strip(),
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
        return cls(
            memory_id=str(value["memory_id"]),
            kind=MemoryKind(str(value["kind"])),
            scope=MemoryScope(str(value["scope"])),
            project_id=None if value.get("project_id") is None else str(value["project_id"]),
            title=str(value["title"]),
            summary=str(value["summary"]),
            body=str(value["body"]),
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
