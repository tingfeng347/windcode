import re

from windcode.extensions.mcp.adapter import McpToolAdapter
from windcode.extensions.mcp.catalog import McpToolDefinition, mcp_tool_wire_name
from windcode.extensions.mcp.runtime import McpRuntime

_VALID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def test_wire_name_is_provider_safe_and_readable() -> None:
    name = mcp_tool_wire_name("tavily-mcp", "tavily_search")
    assert name == "mcp_tavily_search"
    assert _VALID.fullmatch(name)


def test_wire_name_sanitizes_invalid_characters() -> None:
    # Tool names may contain dots via normalize_id; those are illegal on the wire.
    name = mcp_tool_wire_name("srv", "group.do:thing/now")
    assert _VALID.fullmatch(name)


def test_wire_name_does_not_duplicate_existing_mcp_prefix() -> None:
    assert mcp_tool_wire_name("srv", "mcp_search") == "mcp_search"


def test_wire_name_caps_length_with_deterministic_suffix() -> None:
    long_tool = "t" * 100
    first = mcp_tool_wire_name("server", long_tool)
    second = mcp_tool_wire_name("server", long_tool)
    assert len(first) <= 64
    assert first == second
    assert _VALID.fullmatch(first)


def test_wire_name_avoids_collisions_on_overflow() -> None:
    a = mcp_tool_wire_name("server", "t" * 100 + "a")
    b = mcp_tool_wire_name("server", "t" * 100 + "b")
    assert a != b


def test_wire_name_disambiguates_same_tool_from_different_servers() -> None:
    a = mcp_tool_wire_name("server-a", "search", disambiguate=True)
    b = mcp_tool_wire_name("server-b", "search", disambiguate=True)

    assert a.startswith("mcp_search_")
    assert b.startswith("mcp_search_")
    assert a != b


def test_adapter_name_uses_wire_name() -> None:
    definition = McpToolDefinition(
        "mcp:tavily-mcp/tool/tavily_search",
        "tavily-mcp",
        "tavily_search",
        "Search the web",
        {"type": "object"},
        {},
    )
    adapter = McpToolAdapter(definition, McpRuntime({}))
    assert adapter.name == "mcp_tavily_search"
    assert _VALID.fullmatch(adapter.name)
