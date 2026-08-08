import json
from pathlib import Path

from windcode.sessions import SessionStore


def test_append_assigns_sequence_and_parent(tmp_path: Path) -> None:
    store = SessionStore.create(tmp_path, "session")

    first = store.append("message", {"text": "first"})
    second = store.append("message", {"text": "second"}, durable=True)

    assert (first.sequence, second.sequence) == (1, 2)
    assert second.parent_id == first.record_id
    assert store.load_records() == (first, second)
    metadata = json.loads(store.meta_path.read_text())
    assert metadata["next_sequence"] == 3
    assert metadata["head_record_id"] == second.record_id
