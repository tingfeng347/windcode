import json
from pathlib import Path

import pytest

from windcode.domain.tools import ToolContext
from windcode.extensions.mcp.catalog import McpCatalog, McpToolDefinition
from windcode.extensions.mcp.tools import McpToolView, register_mcp_status_tool
from windcode.extensions.models import (
    CapabilityKind,
    CapabilityRecord,
    ExtensionScope,
    ExtensionSource,
)
from windcode.tools.registry import ToolRegistry


def _tool(name: str) -> McpToolDefinition:
    return McpToolDefinition(
        f"mcp:s/tool/{name}", "s", name, f"Tool {name}", {"type": "object"}, {}
    )


def test_large_catalog_requires_per_view_selection() -> None:
    catalog = McpCatalog("s", None, (_tool("a"), _tool("b")), (), ())
    first = McpToolView((catalog,), {}, direct_tool_limit=1)
    second = McpToolView((catalog,), {}, direct_tool_limit=1)

    assert [item.name for item in first.search("Tool")] == ["a", "b"]
    assert first.select("select:mcp:s/tool/a").name == "a"
    assert second.search() == first.search()


@pytest.mark.asyncio
async def test_server_list_omits_disabled_records_unless_explicitly_requested(
    tmp_path: Path,
) -> None:
    source = ExtensionSource(ExtensionScope.USER)
    records = (
        CapabilityRecord("mcp_server:on", "on", CapabilityKind.MCP_SERVER, source),
        CapabilityRecord("mcp_server:off", "off", CapabilityKind.MCP_SERVER, source, enabled=False),
    )
    registry = ToolRegistry()
    register_mcp_status_tool(registry, records, {}, set())
    context = ToolContext(tmp_path, "run", lambda: False)

    default = await registry.execute("list_mcp_servers", context, {})
    all_records = await registry.execute("list_mcp_servers", context, {"include_disabled": True})
    default_payload = json.loads(default.output)
    all_payload = json.loads(all_records.output)

    assert default_payload["server_count"] == 1
    assert [server["id"] for server in default_payload["servers"]] == ["on"]
    assert all_payload["server_count"] == 2
    assert [server["id"] for server in all_payload["servers"]] == ["on", "off"]
