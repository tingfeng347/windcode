import json
import stat
from pathlib import Path

from windcode.extensions.state import ExtensionState, ExtensionStateStore, workspace_identity


def test_state_round_trip_is_atomic_and_private(tmp_path: Path) -> None:
    store = ExtensionStateStore(tmp_path / "data" / "state.json")
    state = store.set_workspace_trust(ExtensionState(), tmp_path, True)
    store.save(state)

    loaded = store.load()
    assert loaded.state == state
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_workspace_symlink_alias_has_same_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    alias = tmp_path / "alias"
    workspace.mkdir()
    alias.symlink_to(workspace, target_is_directory=True)

    assert workspace_identity(workspace).key == workspace_identity(alias).key


def test_corrupt_state_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{broken")
    store = ExtensionStateStore(path)

    result = store.load()

    assert result.state is None
    assert result.diagnostics[0].category == "state_corrupt"
    assert path.read_text() == "{broken"


def test_workspace_object_change_requires_new_trust(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = ExtensionStateStore(tmp_path / "state.json")
    state = store.set_workspace_trust(ExtensionState(), workspace, True)
    workspace.rmdir()
    workspace.mkdir()

    assert not store.is_workspace_trusted(state, workspace)


def test_state_json_contains_no_implicit_secrets(tmp_path: Path) -> None:
    store = ExtensionStateStore(tmp_path / "state.json")
    store.save(ExtensionState(config={"server": {"enabled": True}}))

    assert json.loads(store.path.read_text())["config"] == {"server": {"enabled": True}}
