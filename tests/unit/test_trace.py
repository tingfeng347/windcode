import json
from pathlib import Path

from windcode.domain.events import ToolStarted
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
