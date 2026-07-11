from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(UTC)


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    session_id: str
    created_at: datetime
    updated_at: datetime
    next_sequence: int = 1
    head_record_id: str | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "next_sequence": self.next_sequence,
            "head_record_id": self.head_record_id,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionMetadata:
        return cls(
            schema_version=int(value["schema_version"]),
            session_id=str(value["session_id"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
            next_sequence=int(value["next_sequence"]),
            head_record_id=(
                None if value.get("head_record_id") is None else str(value["head_record_id"])
            ),
            status=SessionStatus(str(value["status"])),
        )


@dataclass(frozen=True, slots=True)
class EventRecord:
    sequence: int
    record_id: str
    parent_id: str | None
    record_type: str
    payload: dict[str, Any]
    created_at: datetime = field(default_factory=utc_now)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "record_id": self.record_id,
            "parent_id": self.parent_id,
            "record_type": self.record_type,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EventRecord:
        raw_payload = value["payload"]
        if not isinstance(raw_payload, dict):
            raise ValueError("event payload must be an object")
        payload = cast(dict[object, object], raw_payload)
        return cls(
            schema_version=int(value["schema_version"]),
            sequence=int(value["sequence"]),
            record_id=str(value["record_id"]),
            parent_id=None if value.get("parent_id") is None else str(value["parent_id"]),
            record_type=str(value["record_type"]),
            payload={str(key): item for key, item in payload.items()},
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    relative_path: str
    sha256: str
    content_length: int
    preview: str
