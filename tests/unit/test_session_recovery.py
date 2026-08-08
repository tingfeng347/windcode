from pathlib import Path

import pytest

from windcode.sessions import SessionCorruptionError, SessionStore


def test_ignores_only_a_corrupt_tail_line(tmp_path: Path) -> None:
    store = SessionStore.create(tmp_path, "session")
    valid = store.append("message", {"text": "ok"}, durable=True)
    with store.events_path.open("a") as stream:
        stream.write('{"incomplete":')

    assert store.load_records() == (valid,)


def test_rejects_corruption_before_the_tail(tmp_path: Path) -> None:
    store = SessionStore.create(tmp_path, "session")
    store.events_path.write_text('{"broken":\n{"also": "broken"}\n')

    with pytest.raises(SessionCorruptionError, match="line 1"):
        store.load_records()


def test_recovers_unfinished_side_effect_without_replaying_it(tmp_path: Path) -> None:
    store = SessionStore.create(tmp_path, "session")
    store.append(
        "tool_started",
        {"call_id": "call-1", "tool": "write_file", "side_effect": True},
        durable=True,
    )

    recovered = store.recover_interrupted_side_effects()

    assert len(recovered) == 1
    assert recovered[0].record_type == "tool_interrupted"
    assert recovered[0].payload["call_id"] == "call-1"
    assert store.recover_interrupted_side_effects() == ()
