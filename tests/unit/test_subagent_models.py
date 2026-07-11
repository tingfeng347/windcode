from datetime import UTC, datetime
from pathlib import Path

import pytest

from windcode.domain.subagents import (
    SubagentRecord,
    SubagentRole,
    SubagentStatus,
    SubagentTaskKind,
    SubagentTaskSpec,
    sort_subagent_records,
    subagent_record_from_dict,
    subagent_record_to_dict,
    transition_subagent,
)


def spec(**changes: object) -> SubagentTaskSpec:
    values: dict[str, object] = {
        "task_name": "inspect_runtime",
        "role": SubagentRole.RESEARCHER,
        "kind": SubagentTaskKind.READ,
        "goal": "Inspect the runtime.",
        "context": "The runtime is under src/windcode/runtime.",
        "expected_output": "A concise report.",
        "verification": ("Cite relevant files.",),
    }
    values.update(changes)
    return SubagentTaskSpec(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_name", "Not Valid"),
        ("goal", ""),
        ("context", " "),
        ("expected_output", ""),
        ("verification", ()),
    ],
)
def test_task_spec_rejects_missing_or_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        spec(**{field: value})


def test_only_worker_can_receive_write_tasks() -> None:
    with pytest.raises(ValueError, match="does not allow write"):
        spec(kind=SubagentTaskKind.WRITE)
    assert (
        spec(role=SubagentRole.WORKER, kind=SubagentTaskKind.WRITE).kind is SubagentTaskKind.WRITE
    )


def test_status_transitions_are_one_way() -> None:
    record = SubagentRecord("child", "parent", "run", 0, spec())
    now = datetime(2026, 7, 11, tzinfo=UTC)
    running = transition_subagent(record, SubagentStatus.RUNNING, now=now)
    completed = transition_subagent(running, SubagentStatus.COMPLETED, now=now)
    integrated = transition_subagent(completed, SubagentStatus.INTEGRATED, now=now)
    assert integrated.started_at == now
    assert integrated.finished_at == now
    with pytest.raises(ValueError, match="invalid subagent status transition"):
        transition_subagent(integrated, SubagentStatus.RUNNING)


def test_records_have_stable_task_order() -> None:
    first = SubagentRecord("first", "parent", "run", 0, spec(task_name="first"))
    second = SubagentRecord(
        "second", "parent", "run", 1, spec(task_name="second"), worktree_path=Path("/tmp/wt")
    )
    assert sort_subagent_records((second, first)) == (first, second)
    assert subagent_record_from_dict(subagent_record_to_dict(second)) == second
