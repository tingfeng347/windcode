from typing import Any

from windcode.domain.events import (
    ApprovalRequested,
    SubagentBlocked,
    SubagentCancelled,
    SubagentCleanup,
    SubagentCompleted,
    SubagentConflict,
    SubagentFailed,
    SubagentIntegrated,
    SubagentProgress,
    SubagentQueued,
    SubagentStarted,
    event_from_dict,
    event_to_dict,
)
from windcode.domain.models import Usage


def common() -> dict[str, Any]:
    return {
        "event_id": "event",
        "session_id": "parent-session",
        "run_id": "parent-run",
        "turn": 1,
        "parent_run_id": "parent-run",
        "subagent_id": "child",
        "task_index": 2,
        "role": "worker",
        "task_name": "fix_tests",
        "summary": "summary",
    }


def test_all_subagent_events_round_trip() -> None:
    fields = common()
    events = (
        SubagentQueued(**fields),
        SubagentStarted(**fields, workspace="/tmp/worktree"),
        SubagentProgress(**fields, activity="testing", usage=Usage(input_tokens=2)),
        SubagentBlocked(**fields, reason="needs clarification"),
        SubagentCompleted(**fields, commit="abc", changed_files=("a.py",)),
        SubagentFailed(**fields, message="failed", category="tool", usage=Usage(input_tokens=3)),
        SubagentCancelled(**fields, reason="parent cancelled", usage=Usage(input_tokens=4)),
        SubagentIntegrated(**fields, commit="def", verification=("pytest: passed",)),
        SubagentConflict(**fields, conflict_files=("a.py",), message="conflict"),
        SubagentCleanup(**fields, removed=False, retained_path="/tmp/worktree", reason="dirty"),
    )
    assert tuple(event_from_dict(event_to_dict(event)) for event in events) == events


def test_old_approval_payload_remains_compatible_and_source_round_trips() -> None:
    old_payload = {
        "kind": "approval_requested",
        "event_id": "event",
        "session_id": "session",
        "run_id": "run",
        "turn": 1,
        "request_id": "request",
        "summary": "run tests",
        "risk": "process",
        "choices": ["allow", "deny"],
    }
    old = event_from_dict(old_payload)
    assert isinstance(old, ApprovalRequested)
    assert old.subagent_id is None

    sourced = ApprovalRequested(
        event_id="event",
        session_id="session",
        run_id="run",
        turn=1,
        request_id="request",
        subagent_id="child",
        subagent_role="worker",
        tool_name="shell",
        arguments_summary="pytest -q",
    )
    assert event_from_dict(event_to_dict(sourced)) == sourced
