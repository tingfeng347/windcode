from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from platformdirs import user_state_path

from windcode.domain.events import AgentEvent, AgentEventType, event_to_dict
from windcode.observability.redaction import redact

_TRANSIENT_EVENT_KINDS = frozenset(
    {"text_delta", "reasoning_status", "tool_progress", "subagent_progress"}
)


class TraceStore:
    def __init__(
        self,
        run_id: str,
        *,
        root: Path | None = None,
        secrets: Iterable[str] = (),
        enabled: bool = True,
        include_tool_arguments: bool = False,
        include_transient_events: bool = False,
        retention_days: int = 14,
        max_total_mb: int = 100,
    ) -> None:
        self.run_id = run_id
        self.root = (root or user_state_path("windcode") / "traces").expanduser().resolve()
        self.path = self.root / f"{run_id}.jsonl"
        self.secrets = tuple(secrets)
        self.enabled = enabled
        self.include_tool_arguments = include_tool_arguments
        self.include_transient_events = include_transient_events
        self.retention_days = retention_days
        self.max_total_bytes = max_total_mb * 1024 * 1024
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)
            self._prune()

    def _prune(self) -> None:
        traces = [path for path in self.root.glob("*.jsonl") if path.is_file()]
        cutoff = datetime.now(UTC).timestamp() - timedelta(days=self.retention_days).total_seconds()
        for path in traces:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

        remaining = sorted(
            (path for path in traces if path.exists()), key=lambda path: path.stat().st_mtime
        )
        total_bytes = sum(path.stat().st_size for path in remaining)
        for path in remaining:
            if total_bytes <= self.max_total_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total_bytes -= size

    def _prepare_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        prepared = dict(payload)
        if not self.include_tool_arguments:
            prepared.pop("arguments", None)
            nested = prepared.get("payload")
            if isinstance(nested, dict):
                nested_copy = cast(dict[str, Any], nested).copy()
                nested_copy.pop("arguments", None)
                prepared["payload"] = nested_copy
        return cast(dict[str, Any], redact(prepared, secrets=self.secrets))

    def write(
        self,
        event: AgentEventType | Mapping[str, Any],
        *,
        elapsed_seconds: float | None = None,
        error_category: str | None = None,
        durable: bool = False,
    ) -> dict[str, Any]:
        if isinstance(event, AgentEvent):
            payload = event_to_dict(event)
            session_id = event.session_id
            run_id = event.run_id
        else:
            payload = dict(event)
            session_id = str(payload.get("session_id", ""))
            run_id = str(payload.get("run_id", self.run_id))

        record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "session_id": session_id,
            "event": self._prepare_payload(payload),
        }
        if elapsed_seconds is not None:
            record["elapsed_seconds"] = elapsed_seconds
        if error_category is not None:
            record["error_category"] = error_category

        kind = str(payload.get("kind", ""))
        if not self.enabled or (
            not self.include_transient_events and kind in _TRANSIENT_EVENT_KINDS
        ):
            return record

        line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        return record
