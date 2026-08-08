import json
from pathlib import Path

import pytest

from windcode.auth import FileCredentialStore
from windcode.config.models import ExtensionConfig
from windcode.domain.tools import ToolContext
from windcode.extensions.hooks.models import HookContext, HookEvent
from windcode.extensions.mcp.catalog import McpToolDefinition
from windcode.extensions.mcp.tools import (
    register_mcp_management_tools,
    register_mcp_status_tool,
)
from windcode.extensions.runtime import RunExtensions
from windcode.extensions.service import ExtensionService
from windcode.extensions.state import ExtensionStateStore
from windcode.sessions import ArtifactStore
from windcode.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_complete_local_plugin_lifecycle(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "extensions" / "complete_plugin"
    state_store = ExtensionStateStore(tmp_path / "state" / "state.json")
    service = ExtensionService(
        ExtensionConfig(enabled=True), tmp_path, state_store, tmp_path / "plugins"
    )

    installed = await service.install_local(fixture, enable=True)
    assert installed.changed
    assert service.snapshot.generation == 0
    assert not any((tmp_path / "plugins").rglob("executed"))

    await service.reload()
    old_snapshot = service.snapshot
    assert {record.kind.value for record in old_snapshot.capabilities} >= {
        "plugin",
        "skill",
        "hook",
        "mcp_server",
    }

    runtime = RunExtensions.create(
        old_snapshot,
        session_id="session",
        run_id="run",
        credential_store=FileCredentialStore(tmp_path / "credentials.json"),
        max_content_bytes=4096,
        connect_timeout=10,
        call_timeout=5,
        artifact_store=ArtifactStore(tmp_path / "session"),
    )
    try:
        await runtime.activate_skill("$review")
        sourced = runtime.drain_context()
        assert sourced[0].source_id.startswith("plugin:complete")
        assert "correctness risks" in sourced[0].content

        outcome = await runtime.hooks.dispatch(
            HookContext(
                1,
                HookEvent.TOOL_BEFORE_POLICY,
                "session",
                "run",
                "call",
                tool_id="dangerous_test_tool",
            )
        )
        assert outcome.rejected == "blocked by complete plugin guard"

        tools = await runtime.mcp_capabilities.search_tools("echo")
        assert [tool.name for tool in tools] == ["echo"]
        adapter = await runtime.mcp_capabilities.adapter(tools[0].stable_id)
        result = await adapter.execute(
            ToolContext(tmp_path, "run", lambda: False),
            adapter.validate_arguments({"text": "hello"}),
        )
        assert result.output == "hello"

        await service.set_enabled("plugin:complete", False)
        await service.reload()
        assert old_snapshot.capabilities != service.snapshot.capabilities
        assert any(record.enabled for record in old_snapshot.capabilities)
        assert not any(
            record.enabled
            for record in service.snapshot.capabilities
            if record.source.plugin_id == "complete"
        )
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_search_auto_selects_and_reuses_cached_tool_in_next_run(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "extensions" / "complete_plugin"
    service = ExtensionService(
        ExtensionConfig(enabled=True),
        tmp_path,
        ExtensionStateStore(tmp_path / "state" / "state.json"),
        tmp_path / "plugins",
    )
    await service.install_local(fixture, enable=True)
    await service.reload()
    catalogs: dict[str, tuple[McpToolDefinition, ...]] = {}
    selected: set[str] = set()
    first = RunExtensions.create(
        service.snapshot,
        session_id="session",
        run_id="first",
        credential_store=FileCredentialStore(tmp_path / "credentials.json"),
        max_content_bytes=4096,
        connect_timeout=10,
        call_timeout=5,
        mcp_tool_catalogs=catalogs,
    )
    first_registry = ToolRegistry()
    register_mcp_management_tools(first_registry, first.mcp_capabilities, selected)
    context = ToolContext(tmp_path, "first", lambda: False)
    try:
        result = await first_registry.execute("search_mcp_tools", context, {"query": "echo"})
        payload = json.loads(result.output)
        call_name = str(payload["call_name"])
        server_id = str(payload["source"])
        assert payload["next_step"].endswith("do not call search_mcp_tools again")
        assert call_name in first_registry.names()
        assert selected == {f"mcp:{server_id}/tool/echo"}
        assert catalogs[server_id][0].name == "echo"

        second = RunExtensions.create(
            service.snapshot,
            session_id="session",
            run_id="second",
            credential_store=FileCredentialStore(tmp_path / "credentials.json"),
            max_content_bytes=4096,
            connect_timeout=10,
            call_timeout=5,
            mcp_tool_catalogs=catalogs,
        )
        second_registry = ToolRegistry()
        try:
            assert await second.mcp_capabilities.register_selected_tools(
                second_registry, selected
            ) == (call_name,)
            assert call_name in second_registry.names()

            register_mcp_status_tool(
                second_registry, service.snapshot.capabilities, catalogs, selected
            )
            status = await second_registry.execute("list_mcp_servers", context, {})
            server = json.loads(status.output)["servers"][0]
            assert server["tool_catalog_cached"] is True
            assert server["cached_tool_count"] == 1
            assert server["selected_tool_count"] == 1
        finally:
            await second.aclose()
    finally:
        await first.aclose()
