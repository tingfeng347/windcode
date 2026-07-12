import pytest

from windcode.domain.subagents import SubagentRole, SubagentTaskKind
from windcode.runtime.subagents.roles import ROLE_POLICIES, resolve_role_tools


def test_builtin_roles_have_expected_task_kinds() -> None:
    assert ROLE_POLICIES[SubagentRole.RESEARCHER].allowed_kinds == {SubagentTaskKind.READ}
    assert ROLE_POLICIES[SubagentRole.VERIFIER].allowed_kinds == {SubagentTaskKind.READ}
    assert ROLE_POLICIES[SubagentRole.WORKER].allowed_kinds == {
        SubagentTaskKind.READ,
        SubagentTaskKind.WRITE,
    }


def test_tool_resolution_is_intersection_of_role_task_and_parent() -> None:
    tools = resolve_role_tools(
        SubagentRole.WORKER,
        SubagentTaskKind.WRITE,
        frozenset({"read_file", "write_file", "network_tool"}),
        frozenset({"read_file", "write_file"}),
    )
    assert tools == {"read_file", "write_file"}


def test_requested_tools_cannot_expand_role_or_parent() -> None:
    with pytest.raises(ValueError, match="exceed role policy"):
        resolve_role_tools(
            SubagentRole.RESEARCHER,
            SubagentTaskKind.READ,
            frozenset({"read_file", "write_file"}),
            frozenset({"write_file"}),
        )
    assert resolve_role_tools(
        SubagentRole.RESEARCHER,
        SubagentTaskKind.READ,
        frozenset({"read_file"}),
        frozenset({"read_file"}),
    ) == {"read_file"}


def test_researcher_inherits_parent_mcp_tools() -> None:
    tools = resolve_role_tools(
        SubagentRole.RESEARCHER,
        SubagentTaskKind.READ,
        frozenset({"read_file", "search_mcp_tools", "tavily-mcp__tavily_search"}),
    )

    assert "search_mcp_tools" in tools
    assert "tavily-mcp__tavily_search" in tools
