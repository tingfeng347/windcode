from pathlib import Path

import pytest

from windcode import Windcode


@pytest.mark.asyncio
async def test_sdk_management_and_snapshot_isolation(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "extensions" / "complete_plugin"
    config = {"extensions": {"enabled": True}}
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=tmp_path) as client:
        installed = await client.install_extension(fixture)
        assert installed.changed
        assert await client.list_extensions() == ()

        await client.reload_extensions()
        disabled_snapshot = client.extension_snapshot
        records = await client.inspect_extension("plugin:complete")
        assert records
        assert not any(record.enabled for record in records)

        changed = await client.set_extension_enabled("plugin:complete", True)
        assert changed.changed and changed.reload_required
        assert client.extension_snapshot is disabled_snapshot

        await client.reload_extensions()
        enabled_snapshot = client.extension_snapshot
        assert enabled_snapshot.generation > disabled_snapshot.generation
        assert any(record.enabled for record in await client.inspect_extension("plugin:complete"))

        await client.set_extension_enabled("plugin:complete", False)
        await client.reload_extensions()
        assert any(record.enabled for record in enabled_snapshot.capabilities)
        assert not any(
            record.enabled
            for record in client.extension_snapshot.capabilities
            if record.source.plugin_id == "complete"
        )
        audit = client.extension_audit()
        assert [record.action for record in audit] == [
            "snapshot_reloaded",
            "plugin_installed",
            "snapshot_reloaded",
            "plugin_state_changed",
            "snapshot_reloaded",
            "plugin_state_changed",
            "snapshot_reloaded",
        ]
        assert audit[-1].generation == client.extension_snapshot.generation


@pytest.mark.asyncio
async def test_sdk_instances_do_not_share_extension_state(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "extensions" / "complete_plugin"
    config = {"extensions": {"enabled": True}}
    async with (
        Windcode.open(config, state_root=tmp_path / "one", workspace=tmp_path) as first,
        Windcode.open(config, state_root=tmp_path / "two", workspace=tmp_path) as second,
    ):
        await first.install_extension(fixture, enable=True)
        await first.reload_extensions()

        assert await first.list_extensions()
        assert await second.list_extensions() == ()
