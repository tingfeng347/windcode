from pathlib import Path

import pytest

from windcode.web.runtime import EventHub, WorkspaceEntry, WorkspaceRuntime, WorkspaceStore


def test_workspace_store_persists_selection_atomically(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    path = tmp_path / "web" / "workspaces.json"
    store = WorkspaceStore(path)

    first_entry = store.add(first)
    second_entry = store.add(second)
    store.select(first_entry.workspace_id)

    reloaded = WorkspaceStore(path)
    assert reloaded.selected == first_entry.workspace_id
    assert {entry.workspace_id for entry in reloaded.list()} == {
        first_entry.workspace_id,
        second_entry.workspace_id,
    }
    assert not tuple(path.parent.glob("workspaces.tmp-*"))


def test_workspace_runtime_uses_project_local_management_config(tmp_path: Path) -> None:
    first = WorkspaceEntry("first", "first", tmp_path / "first")
    second = WorkspaceEntry("second", "second", tmp_path / "second")

    assert WorkspaceRuntime(first).config_file == first.path / ".windcode" / "config.toml"
    assert WorkspaceRuntime(second).config_file == second.path / ".windcode" / "config.toml"


@pytest.mark.asyncio
async def test_event_hub_replays_only_events_after_sequence() -> None:
    hub = EventHub()
    await hub.publish({"type": "one"})
    await hub.publish({"type": "two"})
    subscription = hub.subscribe(after=1)

    event = await anext(subscription)
    await subscription.aclose()

    assert event["type"] == "two"
    assert event["stream_sequence"] == 2
