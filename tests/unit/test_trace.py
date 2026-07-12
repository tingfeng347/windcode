import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from windcode.domain.events import TextDeltaEvent, ToolStarted
from windcode.observability import TraceStore


def test_writes_correlated_redacted_jsonl(tmp_path: Path) -> None:
    trace = TraceStore("run", root=tmp_path, secrets=["secret-value"])
    event = ToolStarted(
        event_id="event",
        session_id="session",
        run_id="run",
        turn=1,
        call_id="call",
        tool_name="shell",
        arguments={"api_key": "secret-value", "command": "echo secret-value"},
    )

    trace.write(event, elapsed_seconds=0.25, durable=True)
    raw = trace.path.read_text()
    record = json.loads(raw)

    assert record["run_id"] == "run"
    assert record["session_id"] == "session"
    assert record["event"]["kind"] == "tool_started"
    assert "arguments" not in record["event"]
    assert "secret-value" not in raw


def test_can_include_non_sensitive_tool_arguments(tmp_path: Path) -> None:
    trace = TraceStore("run", root=tmp_path, include_tool_arguments=True)

    record = trace.write(
        {"run_id": "run", "session_id": "session", "arguments": {"path": "README.md"}}
    )

    assert record["event"]["arguments"] == {"path": "README.md"}


def test_disabled_trace_does_not_create_a_directory(tmp_path: Path) -> None:
    root = tmp_path / "traces"
    trace = TraceStore("run", root=root, enabled=False)

    trace.write({"kind": "run_started", "run_id": "run", "session_id": "session"})

    assert not root.exists()


def test_transient_events_are_opt_in(tmp_path: Path) -> None:
    event = TextDeltaEvent(
        event_id="delta",
        session_id="session",
        run_id="run",
        turn=1,
        text="partial",
    )
    excluded = TraceStore("excluded", root=tmp_path)
    included = TraceStore("included", root=tmp_path, include_transient_events=True)

    excluded.write(event)
    included.write(event)

    assert not excluded.path.exists()
    assert json.loads(included.path.read_text())["event"]["kind"] == "text_delta"


def test_prunes_expired_and_over_capacity_trace_files(tmp_path: Path) -> None:
    expired = tmp_path / "expired.jsonl"
    oldest = tmp_path / "oldest.jsonl"
    newest = tmp_path / "newest.jsonl"
    expired.write_bytes(b"x" * 100)
    oldest.write_bytes(b"x" * 700_000)
    newest.write_bytes(b"x" * 700_000)
    now = datetime.now(UTC)
    os.utime(expired, (now.timestamp(), (now - timedelta(days=15)).timestamp()))
    os.utime(oldest, (now.timestamp(), (now - timedelta(hours=2)).timestamp()))
    os.utime(newest, (now.timestamp(), (now - timedelta(hours=1)).timestamp()))

    TraceStore("run", root=tmp_path, retention_days=14, max_total_mb=1)

    assert not expired.exists()
    assert not oldest.exists()
    assert newest.exists()
