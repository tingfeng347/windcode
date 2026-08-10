from dataclasses import replace
from pathlib import Path
from shutil import copytree

import pytest

from windcode.config.models import ExtensionConfig, McpStdioConfig
from windcode.extensions.models import ActivationState, CapabilityKind, ExtensionScope
from windcode.extensions.service import ExtensionService
from windcode.extensions.state import ExtensionStateStore


@pytest.mark.asyncio
async def test_disabled_service_publishes_empty_snapshot(tmp_path: Path) -> None:
    service = ExtensionService(
        ExtensionConfig(enabled=False),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )

    result = await service.reload()

    assert result.changed
    assert service.snapshot.generation == 1
    assert await service.list_capabilities() == ()


@pytest.mark.asyncio
async def test_state_changes_are_idempotent_and_require_reload(tmp_path: Path) -> None:
    service = ExtensionService(
        ExtensionConfig(enabled=False),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )

    first = await service.set_enabled("plugin:example", True)
    second = await service.set_enabled("plugin:example", True)
    trust = await service.trust_workspace(tmp_path, True)

    assert first.changed and first.reload_required
    assert not second.changed and not second.reload_required
    assert trust.changed and trust.reload_required


@pytest.mark.asyncio
async def test_reload_discovers_project_only_after_explicit_enable(tmp_path: Path) -> None:
    skill = tmp_path / ".windcode" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: review\ndescription: Review\n---\nbody")
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )

    await service.reload()
    review = next(
        record for record in service.snapshot.capabilities if record.kind is CapabilityKind.SKILL
    )
    assert not review.trusted
    await service.trust_workspace(tmp_path, True)
    assert not review.trusted
    await service.reload()
    review = next(
        record for record in service.snapshot.capabilities if record.kind is CapabilityKind.SKILL
    )
    assert review.trusted


@pytest.mark.asyncio
async def test_capability_trust_overrides_workspace_default_per_item(tmp_path: Path) -> None:
    for name in ("review", "test"):
        skill = tmp_path / ".windcode" / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\nbody",
            encoding="utf-8",
        )
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )
    await service.reload()

    await service.trust_capability("skill:review", True)
    await service.reload()

    records = {
        record.capability_id: record
        for record in service.snapshot.capabilities
        if record.kind is CapabilityKind.SKILL
    }
    assert records["skill:review"].trusted
    assert not records["skill:test"].trusted

    await service.trust_workspace(tmp_path, True)
    await service.reload()
    assert all(
        record.trusted
        for record in service.snapshot.capabilities
        if record.kind is CapabilityKind.SKILL
    )

    await service.trust_capability("skill:review", False)
    await service.reload()
    await service.trust_workspace(tmp_path, True)
    await service.reload()
    assert all(
        record.trusted
        for record in service.snapshot.capabilities
        if record.kind is CapabilityKind.SKILL
    )


@pytest.mark.asyncio
async def test_user_capability_trust_is_global_and_per_item(tmp_path: Path) -> None:
    user_root = tmp_path / "user-skills"
    for name in ("global-review", "global-test"):
        skill = user_root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\nbody",
            encoding="utf-8",
        )
    state_path = tmp_path / "state.json"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first = ExtensionService(
        ExtensionConfig(enabled=True),
        first_workspace,
        ExtensionStateStore(state_path),
        tmp_path / "plugins",
        user_skill_root=user_root,
    )
    await first.reload()

    await first.trust_capability("skill:global-review", False, scope=ExtensionScope.USER)
    await first.reload()
    first_records = {
        record.capability_id: record
        for record in first.snapshot.capabilities
        if record.kind is CapabilityKind.SKILL
    }
    assert not first_records["skill:global-review"].trusted
    assert first_records["skill:global-test"].trusted

    second = ExtensionService(
        ExtensionConfig(enabled=True),
        second_workspace,
        ExtensionStateStore(state_path),
        tmp_path / "plugins",
        user_skill_root=user_root,
    )
    await second.reload()
    second_records = {
        record.capability_id: record
        for record in second.snapshot.capabilities
        if record.kind is CapabilityKind.SKILL
    }
    assert not second_records["skill:global-review"].trusted
    assert second_records["skill:global-test"].trusted


@pytest.mark.asyncio
async def test_state_skill_roots_are_combined_and_project_overrides_user(tmp_path: Path) -> None:
    user_root = tmp_path / "user" / "skills"
    project_root = tmp_path / "workspace" / ".windcode" / "skills"
    workspace = tmp_path / "workspace"
    for root, directory, name, description in (
        (user_root, "shared", "shared", "user version"),
        (user_root, "personal", "personal", "personal skill"),
        (project_root, "shared", "shared", "project version"),
    ):
        skill = root / directory
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\nbody",
            encoding="utf-8",
        )
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        workspace,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
        user_skill_root=user_root,
    )
    await service.trust_workspace(workspace, True)

    await service.reload()

    records = tuple(
        record for record in service.snapshot.capabilities if record.kind is CapabilityKind.SKILL
    )
    assert len(records) == 3
    shared = [record for record in records if record.public_name == "shared"]
    assert len(shared) == 2
    user_record = next(record for record in shared if record.source.scope is ExtensionScope.USER)
    project_record = next(
        record for record in shared if record.source.scope is ExtensionScope.PROJECT
    )
    assert user_record.shadowed_by == project_record.source.source_id
    assert project_record.shadowed_by is None


@pytest.mark.asyncio
async def test_inspect_plugin_id_returns_all_installed_components(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )
    await service.install_local(fixture)
    await service.reload()

    records = await service.inspect("plugin:complete")

    assert {record.source.plugin_id for record in records} == {"complete"}
    assert {record.kind.value for record in records} >= {"plugin", "skill", "hook", "mcp_server"}
    assert all(record.permissions.process for record in records)
    assert all(record.permissions.filesystem_read for record in records)


@pytest.mark.asyncio
async def test_plugin_component_fails_closed_when_required_effect_is_undeclared(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    source = copytree(fixture, tmp_path / "source")
    manifest = source / ".windcode-plugin" / "plugin.toml"
    manifest.write_text(
        manifest.read_text().replace('effects = ["read", "process"]', 'effects = ["read"]')
    )
    service = ExtensionService(
        ExtensionConfig(
            enabled=True,
            mcp_servers={"standalone": McpStdioConfig(command="standalone-server", required=False)},
        ),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )
    await service.install_local(source, enable=True)

    result = await service.reload()

    assert any("requires undeclared 'process'" in item.message for item in result.diagnostics)
    plugin_records = tuple(
        record for record in service.snapshot.capabilities if record.source.plugin_id == "complete"
    )
    assert len(plugin_records) == 1
    assert plugin_records[0].activation is ActivationState.FAILED
    assert not plugin_records[0].required
    assert any(
        record.kind is CapabilityKind.MCP_SERVER and record.public_name == "standalone"
        for record in service.snapshot.capabilities
    )


@pytest.mark.asyncio
async def test_plugin_http_mcp_host_must_be_declared(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    source = copytree(fixture, tmp_path / "source")
    manifest = source / ".windcode-plugin" / "plugin.toml"
    manifest.write_text(
        manifest.read_text()
        .replace('effects = ["read", "process"]', 'effects = ["read", "network"]')
        .replace("network_hosts = []", 'network_hosts = ["allowed.example.com"]')
    )
    (source / "mcp" / "server.toml").write_text(
        'transport = "streamable_http"\nurl = "https://blocked.example.com/mcp"\n'
    )
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )
    await service.install_local(source, enable=True)

    result = await service.reload()

    assert any("is not declared in network_hosts" in item.message for item in result.diagnostics)


@pytest.mark.asyncio
async def test_plugin_components_cannot_be_toggled_independently(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    state_path = tmp_path / "state.json"
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        ExtensionStateStore(state_path),
        tmp_path / "plugins",
    )
    await service.install_local(fixture, enable=True)
    before = state_path.read_bytes()

    with pytest.raises(ValueError, match="toggle plugin:complete instead"):
        await service.set_enabled("plugin:complete/mcp_server/analysis", False)

    assert state_path.read_bytes() == before


@pytest.mark.asyncio
async def test_toggling_plugin_clears_legacy_component_overrides(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    store = ExtensionStateStore(tmp_path / "state.json")
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        store,
        tmp_path / "plugins",
    )
    await service.install_local(fixture, enable=True)
    state = store.load().state
    assert state is not None
    store.save(
        replace(
            state,
            enabled={
                "plugin:complete/plugin/complete": True,
                "plugin:complete/mcp_server/analysis": False,
            },
        )
    )
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        store,
        tmp_path / "plugins",
    )

    await service.set_enabled("plugin:complete", False)

    updated = store.load().state
    assert updated is not None
    assert not updated.plugins["complete"].enabled
    assert not any(key.startswith("plugin:complete") for key in updated.enabled)


@pytest.mark.asyncio
async def test_project_mcp_requires_workspace_trust_and_explicit_reload(
    tmp_path: Path,
) -> None:
    service = ExtensionService(
        ExtensionConfig(
            enabled=True,
            mcp_servers={"project": McpStdioConfig(command="never-started")},
            project_mcp_servers=frozenset({"project"}),
        ),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )

    await service.reload()
    record = (await service.inspect("mcp_server:project"))[0]
    assert record.source.scope is ExtensionScope.PROJECT
    assert not record.trusted
    assert record.activation is ActivationState.UNTRUSTED

    await service.trust_workspace(tmp_path, True)
    assert not (await service.inspect("mcp_server:project"))[0].trusted

    await service.reload()
    record = (await service.inspect("mcp_server:project"))[0]
    assert record.trusted
    assert record.activation is ActivationState.AVAILABLE


@pytest.mark.asyncio
async def test_disabled_mcp_is_inspectable_but_not_available(tmp_path: Path) -> None:
    service = ExtensionService(
        ExtensionConfig(
            enabled=True,
            mcp_servers={"disabled": McpStdioConfig(command="never-started", enabled=False)},
        ),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )

    await service.reload()

    record = (await service.inspect("mcp_server:disabled"))[0]
    assert not record.enabled
    assert record.activation is ActivationState.INACTIVE


@pytest.mark.asyncio
async def test_configured_mcp_enabled_state_can_be_toggled(tmp_path: Path) -> None:
    service = ExtensionService(
        ExtensionConfig(
            enabled=True,
            mcp_servers={"toggleable": McpStdioConfig(command="never-started")},
        ),
        tmp_path,
        ExtensionStateStore(tmp_path / "state.json"),
        tmp_path / "plugins",
    )

    await service.reload()
    await service.set_enabled("mcp_server:toggleable", False)
    await service.reload()
    disabled = (await service.inspect("mcp_server:toggleable"))[0]
    assert not disabled.enabled
    assert disabled.activation is ActivationState.INACTIVE

    await service.set_enabled("mcp_server:toggleable", True)
    await service.reload()
    enabled = (await service.inspect("mcp_server:toggleable"))[0]
    assert enabled.enabled
    assert enabled.activation is ActivationState.AVAILABLE


@pytest.mark.asyncio
async def test_reinstall_same_plugin_preserves_enabled_state_and_state_bytes(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "extensions" / "complete_plugin"
    state_path = tmp_path / "state.json"
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        ExtensionStateStore(state_path),
        tmp_path / "plugins",
    )
    await service.install_local(fixture, enable=True)
    before = state_path.read_bytes()

    repeated = await service.install_local(fixture)

    assert not repeated.changed
    assert state_path.read_bytes() == before
    await service.reload()
    assert any(record.enabled for record in await service.inspect("plugin:complete"))
