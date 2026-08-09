from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from windcode.config import AppConfig, PermissionMode
from windcode.domain.events import RunRequest
from windcode.domain.tools import ToolEffect
from windcode.extensions import ExtensionSnapshot
from windcode.extensions.mcp.catalog import McpToolDefinition
from windcode.extensions.mcp.tools import (
    SearchMcpToolsTool,
    register_mcp_management_tools,
    register_mcp_status_tool,
)
from windcode.extensions.runtime import RunExtensions
from windcode.extensions.skills.tools import register_skill_tools
from windcode.instructions import InstructionBlock, load_instructions
from windcode.policy import CommandRule, PolicyEngine
from windcode.policy.rules import CommandRuleStore
from windcode.sandbox import SandboxBackend, SandboxPolicy, SandboxPreset, create_sandbox_backend
from windcode.sessions import SessionStore
from windcode.tools import ToolRegistry
from windcode.tools.shell import ShellTool


@dataclass(frozen=True, slots=True)
class ParentAccess:
    permission_mode: PermissionMode
    sandbox_preset: SandboxPreset
    sandbox: SandboxBackend | None
    sandbox_policy: SandboxPolicy
    registry: ToolRegistry
    policy: PolicyEngine
    child_tools: ToolRegistry
    instructions: tuple[InstructionBlock, ...]


class ParentAccessBuilder:
    """Builds the parent permission, sandbox, tool, and child capability view."""

    def __init__(
        self,
        config: AppConfig,
        *,
        state_root: Path,
        base_tools: ToolRegistry,
    ) -> None:
        self.config = config
        self.state_root = state_root
        self.base_tools = base_tools

    def prepare(
        self,
        workspace: Path,
        session: SessionStore,
        request: RunRequest,
        run_extensions: RunExtensions,
        extension_snapshot: ExtensionSnapshot,
        mcp_tool_catalogs: dict[str, tuple[McpToolDefinition, ...]],
        mcp_selected_tools: set[str],
    ) -> ParentAccess:
        mode = (
            PermissionMode(request.permission_mode)
            if request.permission_mode is not None
            else self.config.permission.mode
        )
        preset = SandboxPreset(self.config.sandbox.preset)
        writable_roots = tuple(
            (workspace / value).resolve()
            if not Path(value).is_absolute()
            else Path(value).resolve()
            for value in self.config.sandbox.writable_roots
        )
        sandbox, sandbox_policy = create_sandbox_backend(
            workspace,
            preset=preset,
            writable_roots=writable_roots,
            network_enabled=self.config.sandbox.network_enabled,
        )
        registry = self._parent_registry(
            run_extensions,
            extension_snapshot,
            mcp_tool_catalogs,
            mcp_selected_tools,
            sandbox,
            sandbox_policy,
        )
        policy = PolicyEngine(
            mode,
            sandbox_enabled=preset is not SandboxPreset.DANGER_FULL_ACCESS,
            sandbox_available=sandbox is not None and sandbox.status.available,
            rule_store=CommandRuleStore(self.state_root, workspace),
        )
        self._restore_session_approvals(session, workspace, policy)
        child_tools = registry.clone()
        if "search_mcp_tools" in registry.names():
            search_mcp_tools = registry.get("search_mcp_tools")
            if isinstance(search_mcp_tools, SearchMcpToolsTool):
                search_mcp_tools.add_registry(child_tools)
        return ParentAccess(
            mode,
            preset,
            sandbox,
            sandbox_policy,
            registry,
            policy,
            child_tools,
            load_instructions(workspace, workspace_root=workspace),
        )

    def _parent_registry(
        self,
        run_extensions: RunExtensions,
        extension_snapshot: ExtensionSnapshot,
        mcp_tool_catalogs: dict[str, tuple[McpToolDefinition, ...]],
        mcp_selected_tools: set[str],
        sandbox: SandboxBackend | None,
        sandbox_policy: SandboxPolicy,
    ) -> ToolRegistry:
        registry = self.base_tools.clone()
        register_skill_tools(registry, run_extensions.skills, run_extensions.activate_skill)
        register_mcp_status_tool(
            registry,
            extension_snapshot.capabilities,
            mcp_tool_catalogs,
            mcp_selected_tools,
        )
        if run_extensions.mcp.server_ids:
            register_mcp_management_tools(
                registry, run_extensions.mcp_capabilities, mcp_selected_tools
            )
        registry.register(
            ShellTool(
                sandbox=sandbox,
                sandbox_policy=sandbox_policy,
                default_timeout=self.config.budgets.shell_timeout_seconds,
            ),
            replace=True,
        )
        return registry

    def _restore_session_approvals(
        self,
        session: SessionStore,
        workspace: Path,
        policy: PolicyEngine,
    ) -> None:
        for record in session.load_records():
            if record.record_type != "session_approval":
                continue
            if record.payload.get("workspace") != str(workspace):
                continue
            tool_name = record.payload.get("tool_name")
            raw_rule = record.payload.get("rule")
            if isinstance(raw_rule, Mapping):
                try:
                    policy.restore_session_rule(CommandRule.model_validate(raw_rule))
                except ValueError:
                    pass
                continue
            raw_effects = record.payload.get("effects")
            if not isinstance(tool_name, str) or not isinstance(raw_effects, list):
                continue
            try:
                effects = frozenset(
                    ToolEffect(str(effect)) for effect in cast(list[object], raw_effects)
                )
            except ValueError:
                continue
            policy.restore_session_approval(tool_name, effects)
