from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass

from windcode.extensions.discovery import DiscoveryResult
from windcode.extensions.models import DiagnosticSeverity, ExtensionSnapshot


def config_fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class SnapshotCandidate:
    snapshot: ExtensionSnapshot
    publishable: bool


def build_candidate(
    result: DiscoveryResult, *, generation: int, config: object
) -> SnapshotCandidate:
    snapshot = ExtensionSnapshot(
        generation,
        config_fingerprint(config),
        result.records,
        result.definitions,
        result.diagnostics,
    )
    required_sources = {item.source.source_id for item in result.records if item.required}
    blocked = any(
        diagnostic.severity is DiagnosticSeverity.ERROR and diagnostic.source_id in required_sources
        for diagnostic in result.diagnostics
    )
    return SnapshotCandidate(snapshot, not blocked)


class SnapshotPublisher:
    def __init__(self, initial: ExtensionSnapshot | None = None) -> None:
        self._current = initial or ExtensionSnapshot(0, config_fingerprint({}))
        self._write_lock = threading.Lock()

    @property
    def current(self) -> ExtensionSnapshot:
        return self._current

    def publish(self, candidate: SnapshotCandidate) -> bool:
        if not candidate.publishable:
            return False
        with self._write_lock:
            if candidate.snapshot.generation <= self._current.generation:
                raise ValueError("snapshot generation must increase")
            self._current = candidate.snapshot
        return True
