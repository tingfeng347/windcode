from __future__ import annotations

from dataclasses import dataclass

from windcode.domain.subagents import SubagentRole, SubagentTaskKind

_READ_TOOLS = frozenset({"read_file", "glob", "grep", "shell"})
_WRITE_TOOLS = frozenset(
    {"read_file", "write_file", "edit_file", "apply_patch", "glob", "grep", "shell"}
)


@dataclass(frozen=True, slots=True)
class RolePolicy:
    role: SubagentRole
    default_tools: frozenset[str]
    allowed_kinds: frozenset[SubagentTaskKind]
    system_instructions: str


ROLE_POLICIES: dict[SubagentRole, RolePolicy] = {
    SubagentRole.RESEARCHER: RolePolicy(
        role=SubagentRole.RESEARCHER,
        default_tools=_READ_TOOLS,
        allowed_kinds=frozenset({SubagentTaskKind.READ}),
        system_instructions="Explore the workspace and return evidence without modifying files.",
    ),
    SubagentRole.WORKER: RolePolicy(
        role=SubagentRole.WORKER,
        default_tools=_WRITE_TOOLS,
        allowed_kinds=frozenset({SubagentTaskKind.READ, SubagentTaskKind.WRITE}),
        system_instructions="Complete the assigned task and run the requested verification.",
    ),
    SubagentRole.VERIFIER: RolePolicy(
        role=SubagentRole.VERIFIER,
        default_tools=_READ_TOOLS,
        allowed_kinds=frozenset({SubagentTaskKind.READ}),
        system_instructions="Independently verify the requested behavior without modifying files.",
    ),
}


def resolve_role_tools(
    role: SubagentRole,
    kind: SubagentTaskKind,
    parent_tools: frozenset[str],
    requested_tools: frozenset[str] | None = None,
) -> frozenset[str]:
    policy = ROLE_POLICIES[role]
    if kind not in policy.allowed_kinds:
        raise ValueError(f"role {role.value} does not allow {kind.value} tasks")
    network_tools = frozenset(
        name
        for name in parent_tools
        if "__" in name
        or name in {"list_mcp_servers", "search_mcp_tools", "read_mcp_resource", "get_mcp_prompt"}
    )
    allowed_tools = policy.default_tools | network_tools
    if requested_tools is not None:
        unknown = requested_tools - allowed_tools
        if unknown:
            raise ValueError(f"requested tools exceed role policy: {', '.join(sorted(unknown))}")
    selected = allowed_tools if requested_tools is None else requested_tools
    return selected & parent_tools
