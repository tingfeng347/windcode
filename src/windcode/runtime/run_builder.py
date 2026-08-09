from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

from windcode.config import AppConfig, PermissionMode
from windcode.domain.events import RunRequest
from windcode.domain.messages import Message, heal_dangling_tool_calls, message_from_dict
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
from windcode.providers import ModelTarget
from windcode.runtime.resources import RunResources
from windcode.runtime.subagents.factory import ChildRunScope
from windcode.sandbox import SandboxBackend, SandboxPolicy, SandboxPreset, create_sandbox_backend
from windcode.sessions import ArtifactStore, SessionStore, ancestor_chain
from windcode.tools import ToolRegistry
from windcode.tools.shell import ShellTool


@dataclass(frozen=True, slots=True)
class ParentRunPreparation:
    workspace: Path
    existing_session: bool
    session: SessionStore
    initial_messages: tuple[Message, ...]
    run_id: str
    artifact_store: ArtifactStore


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


class RunBuilder:
    def __init__(
        self,
        config: AppConfig,
        *,
        state_root: Path,
        base_tools: ToolRegistry,
        model_chain: Callable[[str | None], tuple[ModelTarget, ...]],
    ) -> None:
        self.config = config
        self.state_root = state_root
        self.base_tools = base_tools
        self.model_chain = model_chain

    @staticmethod
    def _summary(prompt: str, *, limit: int = 60) -> str:
        summary = " ".join(prompt.split())
        if len(summary) <= limit:
            return summary
        return summary[: limit - 3].rstrip() + "..."

    def prepare_parent(self, request: RunRequest) -> ParentRunPreparation:
        workspace = request.workspace.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {workspace}")
        sessions_root = self.state_root / "sessions"
        existing = (
            request.session_id is not None
            and (sessions_root / request.session_id / "meta.json").exists()
        )
        if existing:
            assert request.session_id is not None
            session = SessionStore.open(sessions_root, request.session_id)
        else:
            session = SessionStore.create(sessions_root, request.session_id)
        if not session.metadata.summary:
            session.set_summary(self._summary(request.prompt))
        initial_messages: tuple[Message, ...] = ()
        if existing and session.metadata.head_record_id is not None:
            records = ancestor_chain(session.load_records(), session.metadata.head_record_id)
            initial_messages = heal_dangling_tool_calls(
                tuple(
                    message_from_dict(record.payload)
                    for record in records
                    if record.record_type == "conversation_message"
                )
            )
        return ParentRunPreparation(
            workspace,
            existing,
            session,
            initial_messages,
            uuid4().hex,
            ArtifactStore(session.session_dir),
        )

    def resources(self, preparation: ParentRunPreparation) -> RunResources:
        return RunResources.create(
            session=preparation.session,
            run_id=preparation.run_id,
            state_root=self.state_root,
            artifact_store=preparation.artifact_store,
            trace_config=self.config.trace,
            context_config=self.config.context,
        )

    def prepare_parent_access(
        self,
        preparation: ParentRunPreparation,
        request: RunRequest,
        run_extensions: RunExtensions,
        extension_snapshot: ExtensionSnapshot,
        mcp_tool_catalogs: dict[str, tuple[McpToolDefinition, ...]],
        mcp_selected_tools: set[str],
    ) -> ParentAccess:
        workspace = preparation.workspace
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
        self._restore_session_approvals(preparation.session, workspace, policy)
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

    def child_scope(
        self,
        parent_tools: ToolRegistry,
        extension_snapshot: ExtensionSnapshot,
        *,
        default_model: str | None,
    ) -> ChildRunScope:
        return ChildRunScope(
            config=self.config,
            state_root=self.state_root,
            parent_tools=parent_tools,
            model_chain=lambda model: self.model_chain(model or default_model),
            extension_snapshot=extension_snapshot,
        )
