import json
import shutil
from dataclasses import replace
from pathlib import Path

from windcode.auth import FileCredentialStore
from windcode.extensions.state import ExtensionStateStore
from windcode.memory import MemoryActivation, MemoryStore
from windcode.sessions import SessionStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "legacy_state"


def test_session_v1_fixture_can_be_read_appended_and_reopened(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    shutil.copytree(FIXTURES / "session_v1", sessions_root / "legacy-session")

    store = SessionStore.open(sessions_root, "legacy-session")
    assert store.metadata.summary == ""
    assert store.load_records()[0].payload == {"text": "legacy message"}

    appended = store.append("message", {"text": "new message"}, durable=True)
    reopened = SessionStore.open(sessions_root, "legacy-session")

    assert appended.sequence == 2
    assert reopened.metadata.head_record_id == appended.record_id
    assert [record.payload["text"] for record in reopened.load_records()] == [
        "legacy message",
        "new message",
    ]


def test_extension_state_v1_fixture_can_be_updated_and_reopened(tmp_path: Path) -> None:
    path = tmp_path / "extensions" / "state.json"
    path.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "extension_state_v1.json", path)
    store = ExtensionStateStore(path)

    loaded = store.load().state
    assert loaded is not None
    assert loaded.enabled == {"skill:legacy": True}

    store.save(replace(loaded, enabled={**loaded.enabled, "skill:new": False}))
    reopened = ExtensionStateStore(path).load().state

    assert reopened is not None
    assert reopened.version == 1
    assert reopened.enabled == {"skill:legacy": True, "skill:new": False}


def test_legacy_auth_fixture_can_be_updated_and_reopened(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    shutil.copyfile(FIXTURES / "auth.json", path)

    store = FileCredentialStore(path)
    assert store.get("legacy-provider") == "fixture-secret-not-real"
    store.set("new-provider", "new-fixture-secret")

    reopened = FileCredentialStore(path)
    assert reopened.get("legacy-provider") == "fixture-secret-not-real"
    assert reopened.get("new-provider") == "new-fixture-secret"
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "legacy-provider",
        "new-provider",
    }


def test_memory_v1_fixture_can_be_rebuilt_updated_and_reopened(tmp_path: Path) -> None:
    record_path = tmp_path / "memory" / "records" / "user" / "user_profile" / "legacy-memory.md"
    record_path.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "memory_v1.md", record_path)

    store = MemoryStore(tmp_path / "memory")
    assert store.rebuild() == 1
    legacy = store.get("legacy-memory")
    assert legacy.activation is MemoryActivation.ALWAYS
    assert legacy.priority == 80

    updated = store.update("legacy-memory", summary="Updated legacy preference")
    reopened = MemoryStore(tmp_path / "memory").get("legacy-memory")

    assert updated.version == 2
    assert reopened.summary == "Updated legacy preference"
