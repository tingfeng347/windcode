from pathlib import Path

from windcode.sessions import SessionStore, ancestor_chain, create_branch


def test_creates_branch_without_changing_original_history(tmp_path: Path) -> None:
    store = SessionStore.create(tmp_path, "session")
    root = store.append("message", {"text": "root"})
    original = store.append("message", {"text": "original"})

    branch = create_branch(store, root.record_id, "message", {"text": "branch"})
    records = store.load_records()

    assert branch.parent_id == root.record_id
    assert ancestor_chain(records, original.record_id) == (root, original)
    assert ancestor_chain(records, branch.record_id) == (root, branch)
