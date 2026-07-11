from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from platformdirs import user_state_path

from windcode.domain.events import AgentEvent, AgentEventType, event_to_dict
from windcode.observability.redaction import redact


class TraceStore:
    def __init__(
        self,
        run_id: str,
        *,
        root: Path | None = None,
        secrets: Iterable[str] = (),
        include_tool_arguments: bool = False,
    ) -> None:
        self.run_id = run_id
        self.root = (root or user_state_path("windcode") / "traces").expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f"{run_id}.jsonl"
        self.secrets = tuple(secrets)
        self.include_tool_arguments = include_tool_arguments

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

        line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        return record
