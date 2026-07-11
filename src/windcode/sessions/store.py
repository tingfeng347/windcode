from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from windcode.sessions.models import EventRecord, SessionMetadata, SessionStatus, utc_now


class SessionCorruptionError(ValueError):
    pass


class SessionStore:
    def __init__(self, sessions_root: Path, metadata: SessionMetadata) -> None:
        self.sessions_root = sessions_root.expanduser().resolve()
        self.metadata = metadata
        self.session_dir = self.sessions_root / metadata.session_id
        self.events_path = self.session_dir / "events.jsonl"
        self.meta_path = self.session_dir / "meta.json"
        self._lock = threading.Lock()

    @classmethod
    def create(cls, sessions_root: Path, session_id: str | None = None) -> SessionStore:
        now = utc_now()
        metadata = SessionMetadata(
            session_id=session_id or uuid4().hex,
            created_at=now,
            updated_at=now,
        )
        store = cls(sessions_root, metadata)
        store.session_dir.mkdir(parents=True, exist_ok=False)
        (store.session_dir / "artifacts").mkdir()
        store.events_path.touch()
        store._write_metadata(durable=True)
        return store

    @classmethod
    def open(cls, sessions_root: Path, session_id: str) -> SessionStore:
        meta_path = sessions_root.expanduser().resolve() / session_id / "meta.json"
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("metadata must be an object")
            metadata = SessionMetadata.from_dict(cast(dict[str, Any], raw))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SessionCorruptionError(f"cannot load {meta_path}: {exc}") from exc
        return cls(sessions_root, metadata)

    def _write_metadata(self, *, durable: bool) -> None:
        temporary = self.meta_path.with_suffix(f".tmp-{uuid4().hex}")
        data = json.dumps(self.metadata.to_dict(), ensure_ascii=True, sort_keys=True) + "\n"
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                if durable:
                    os.fsync(stream.fileno())
            temporary.replace(self.meta_path)
            if durable:
                directory_fd = os.open(self.session_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def append(
        self,
        record_type: str,
        payload: dict[str, Any],
        *,
        parent_id: str | None = None,
        durable: bool = False,
    ) -> EventRecord:
        with self._lock:
            record = EventRecord(
                sequence=self.metadata.next_sequence,
                record_id=uuid4().hex,
                parent_id=self.metadata.head_record_id if parent_id is None else parent_id,
                record_type=record_type,
                payload=payload,
            )
            line = json.dumps(record.to_dict(), ensure_ascii=True, sort_keys=True) + "\n"
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                if durable:
                    os.fsync(stream.fileno())
            self.metadata = replace(
                self.metadata,
                updated_at=utc_now(),
                next_sequence=record.sequence + 1,
                head_record_id=record.record_id,
            )
            self._write_metadata(durable=durable)
            return record

    def set_status(self, status: SessionStatus, *, durable: bool = True) -> None:
        with self._lock:
            self.metadata = replace(self.metadata, status=status, updated_at=utc_now())
            self._write_metadata(durable=durable)

    def load_records(self) -> tuple[EventRecord, ...]:
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise SessionCorruptionError(f"cannot read {self.events_path}: {exc}") from exc

        records: list[EventRecord] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError("record must be an object")
                record = EventRecord.from_dict(cast(dict[str, Any], raw))
                expected = records[-1].sequence + 1 if records else 1
                if record.sequence != expected:
                    raise ValueError(f"expected sequence {expected}, got {record.sequence}")
                records.append(record)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if index == len(lines) - 1:
                    break
                raise SessionCorruptionError(
                    f"invalid event record at line {index + 1}: {exc}"
                ) from exc
        return tuple(records)

    def recover_interrupted_side_effects(self) -> tuple[EventRecord, ...]:
        records = self.load_records()
        finished = {
            str(record.payload.get("call_id"))
            for record in records
            if record.record_type in {"tool_finished", "tool_interrupted"}
        }
        pending = [
            record
            for record in records
            if record.record_type == "tool_started"
            and record.payload.get("side_effect") is True
            and str(record.payload.get("call_id")) not in finished
        ]
        recovered: list[EventRecord] = []
        for record in pending:
            recovered.append(
                self.append(
                    "tool_interrupted",
                    {
                        "call_id": record.payload.get("call_id"),
                        "reason": "session ended before a durable result was recorded",
                    },
                    durable=True,
                )
            )
        return tuple(recovered)
