from datetime import UTC, datetime
from pathlib import Path

from windcode.domain.events import (
    RunRequest,
    RunStarted,
    ToolFinished,
    event_from_dict,
    event_to_dict,
)
from windcode.domain.messages import (
    AttachmentBlock,
    Message,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    message_from_dict,
    message_to_dict,
)
from windcode.domain.tools import ToolResult


def test_event_serializes_common_fields_and_kind() -> None:
    event = RunStarted(event_id="e1", session_id="s1", run_id="r1", turn=0, prompt="fix")

    payload = event_to_dict(event)

    assert payload["kind"] == "run_started"
    assert payload["event_id"] == "e1"
    assert payload["prompt"] == "fix"
    assert isinstance(payload["created_at"], str)


def test_run_request_keeps_workspace_path() -> None:
    request = RunRequest(prompt="fix", workspace=Path("/tmp/project"))

    assert request.workspace == Path("/tmp/project")


def test_event_round_trip() -> None:
    event = ToolFinished(
        event_id="event",
        session_id="session",
        run_id="run",
        turn=2,
        call_id="call",
        result=ToolResult(output="ok", data={"exit_code": 0}),
    )

    restored = event_from_dict(event_to_dict(event))

    assert restored == event


def test_message_round_trip_preserves_every_content_block() -> None:
    message = Message(
        role=Role.ASSISTANT,
        content=(
            TextBlock("working"),
            ReasoningBlock("checking", {"signature": "opaque"}),
            ToolCallBlock("call", "read_file", {"path": "README.md"}),
            ToolResultBlock("call", "read_file", "contents", artifact_ref="artifact.txt"),
            AttachmentBlock("image/png", "artifact://image", "screenshot"),
        ),
        created_at=datetime(2026, 7, 11, 12, 30, tzinfo=UTC),
        provider_metadata={"provider": {"request_id": "request"}},
    )

    restored = message_from_dict(message_to_dict(message))

    assert restored == message
