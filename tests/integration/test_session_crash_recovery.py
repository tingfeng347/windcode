from pathlib import Path

from windcode.sessions import SessionStore, ancestor_chain, create_branch


def test_interrupted_side_effect_is_recorded_once_and_never_replayed(tmp_path: Path) -> None:
    store = SessionStore.create(tmp_path, "session")
    started = store.append(
        "tool_started",
        {"call_id": "write", "tool_name": "write_file", "side_effect": True},
        durable=True,
    )

    reopened = SessionStore.open(tmp_path, "session")
    recovered = reopened.recover_interrupted_side_effects()

    assert len(recovered) == 1
    assert recovered[0].payload["call_id"] == "write"
    assert reopened.recover_interrupted_side_effects() == ()
    branch = create_branch(reopened, started.record_id, "message", {"text": "continue safely"})
    assert ancestor_chain(reopened.load_records(), branch.record_id) == (started, branch)


def test_durable_tool_result_prevents_false_interruption(tmp_path: Path) -> None:
    store = SessionStore.create(tmp_path, "session")
    store.append("tool_started", {"call_id": "shell", "side_effect": True}, durable=True)
    store.append("tool_finished", {"call_id": "shell", "exit_code": 0}, durable=True)
    assert SessionStore.open(tmp_path, "session").recover_interrupted_side_effects() == ()
