from __future__ import annotations

from typing import Any

from windcode.sessions.models import EventRecord
from windcode.sessions.store import SessionStore


def ancestor_chain(records: tuple[EventRecord, ...], record_id: str) -> tuple[EventRecord, ...]:
    by_id = {record.record_id: record for record in records}
    chain: list[EventRecord] = []
    cursor: str | None = record_id
    visited: set[str] = set()
    while cursor is not None:
        if cursor in visited:
            raise ValueError("session record graph contains a cycle")
        visited.add(cursor)
        try:
            record = by_id[cursor]
        except KeyError as exc:
            raise ValueError(f"unknown record id: {cursor}") from exc
        chain.append(record)
        cursor = record.parent_id
    chain.reverse()
    return tuple(chain)


def create_branch(
    store: SessionStore,
    parent_id: str,
    record_type: str,
    payload: dict[str, Any],
) -> EventRecord:
    ancestor_chain(store.load_records(), parent_id)
    return store.append(record_type, payload, parent_id=parent_id, durable=True)
