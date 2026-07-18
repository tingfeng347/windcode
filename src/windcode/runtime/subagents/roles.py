from __future__ import annotations

from dataclasses import dataclass

from windcode.domain.subagents import SubagentRole, SubagentTaskKind

_READ_TOOLS = frozenset({"read_file", "glob", "grep", "shell", "search_skills", "load_skill"})
_WRITE_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "apply_patch",
        "glob",
        "grep",
        "shell",
        "search_skills",
        "load_skill",
    }
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
        if name.startswith("mcp_")
        or name in {"list_mcp_servers", "search_mcp_tools", "read_mcp_resource", "get_mcp_prompt"}
    )
    # Task kind is an independent capability boundary. In particular, a worker
    # may perform either kind of task, but a worker/read task must remain just as
    # read-only as researcher/read and verifier/read.
    kind_tools = (
        policy.default_tools & _READ_TOOLS
        if kind is SubagentTaskKind.READ
        else policy.default_tools
    )
    allowed_tools = kind_tools | network_tools
    # ``requested_tools`` is model-authored input, so treat it as a narrowing
    # hint rather than a second policy boundary. A model can accidentally copy a
    # write tool into a read-only researcher task; denying that tool is enough to
    # preserve the role boundary and should not prevent the remaining research
    # tools from starting.
    selected = allowed_tools if requested_tools is None else requested_tools & allowed_tools
    return selected & parent_tools
