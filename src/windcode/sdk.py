from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast
from uuid import uuid4

from platformdirs import user_state_path

from windcode.auth import CredentialStore, FileCredentialStore
from windcode.config import AppConfig, PermissionMode, save_memory_config, save_model_config
from windcode.context import TokenEstimator
from windcode.domain.events import (
    AgentEventType,
    ApprovalResponse,
    MemoryEvent,
    RunRequest,
    RunResponse,
    RunResult,
)
from windcode.domain.messages import (
    Message,
    Role,
    TextBlock,
    heal_dangling_tool_calls,
    message_from_dict,
)
from windcode.domain.subagents import SubagentRecord, SubagentResult
from windcode.domain.tools import Tool, ToolContext, ToolEffect
from windcode.extensions.commands import CommandRoute
from windcode.extensions.hooks.models import HookContext, HookEvent
from windcode.extensions.mcp.catalog import McpToolDefinition
from windcode.extensions.mcp.tools import (
    register_mcp_management_tools,
    register_mcp_status_tool,
)
from windcode.extensions.models import (
    CapabilityKind,
    CapabilityRecord,
    ExtensionSnapshot,
    ManagementResult,
)
from windcode.extensions.plugins.installer import InstallResult
from windcode.extensions.runtime import RunExtensions
from windcode.extensions.service import ExtensionService
from windcode.extensions.skills.loader import SkillLoader
from windcode.extensions.skills.tools import (
    SkillCatalog,
    SkillSearchResult,
    register_skill_tools,
)
from windcode.extensions.state import ExtensionStateStore, ManagementAuditRecord
from windcode.instructions import load_instructions
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
    assess_core_project_fact,
    assess_experience,
    classify_memory_intent,
    explicitly_always_project_fact,
    is_project_fact,
    refine_memory,
    should_assess_experience,
)
from windcode.observability import DynamicRedactor, TraceStore
from windcode.policy import PolicyEngine, PolicyRequest
from windcode.providers import ModelTarget, ModelTransport, TransportRegistry
from windcode.runtime.control import RunBudgets, RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.loop import AgentLoop
from windcode.runtime.prompts import build_system_prompt
from windcode.runtime.scheduler import ScheduledCall, ToolScheduler
from windcode.runtime.subagents import (
    ChildRuntimeFactory,
    SubagentCoordinator,
    VerificationRunner,
)
from windcode.sandbox import BubblewrapSandbox, detect_bubblewrap
from windcode.sessions import (
    ArtifactStore,
    EventRecord,
    SessionMetadata,
    SessionStore,
    ancestor_chain,
    create_branch,
)
from windcode.tools import (
    ToolRegistry,
    add_subagent_tools,
    create_builtin_registry,
    register_memory_tools,
)
from windcode.tools.shell import ShellTool
from windcode.worktrees import WorktreeManager


class RunHandle:
    def __init__(
        self,
        task: asyncio.Task[RunResult],
        event_bus: EventBus,
        control: RunControl,
        *,
        after_sequence: int = 0,
        coordinator: SubagentCoordinator,
        policy: PolicyEngine,
        loop: AgentLoop,
    ) -> None:
        self._task = task
        self._event_bus = event_bus
        self._control = control
        self._after_sequence = after_sequence
        self._coordinator = coordinator
        self._policy = policy
        self._loop = loop
        self._result: RunResult | None = None
        self._result_lock = asyncio.Lock()

    def __aiter__(self) -> AsyncIterator[AgentEventType]:
        return self._event_bus.subscribe(after_sequence=self._after_sequence)

    async def respond(self, response: RunResponse) -> None:
        try:
            self._control.respond(response)
        except ValueError:
            if not isinstance(response, ApprovalResponse):
                raise
            self._coordinator.approvals.respond(response)

    async def cancel(self) -> None:
        await self._coordinator.shutdown("parent run cancelled")
        self._control.cancel()
        if not self._task.done():
            self._task.cancel()
        await self.result()

    async def result(self) -> RunResult:
        if self._result is not None:
            return self._result
        async with self._result_lock:
            if self._result is None:
                self._result = await self._task
            return self._result

    async def compact(self) -> None:
        if self.done:
            raise RuntimeError("cannot compact a completed run")
        self._control.request_compaction()

    @property
    def permission_mode(self) -> PermissionMode:
        return self._policy.mode

    def set_permission_mode(self, mode: PermissionMode | str) -> PermissionMode:
        selected = PermissionMode(mode)
        previous = self._policy.mode
        self._policy.set_mode(selected)
        self._loop.system_prompt = self._loop.system_prompt.replace(
            f"权限模式: {previous.value}.",
            f"权限模式: {selected.value}.",
        )
        self._coordinator.set_permission_mode(selected)
        return selected

    @property
    def done(self) -> bool:
        return self._task.done()

    def subagents(self) -> tuple[SubagentRecord, ...]:
        return self._coordinator.list()

    async def cancel_subagent(self, subagent_id: str) -> None:
        if self.done:
            raise RuntimeError("cannot cancel a subagent after the parent run has ended")
        await self._coordinator.cancel(subagent_id)

    async def integrate_subagent(
        self,
        subagent_id: str,
        *,
        verification_commands: tuple[str, ...] = (),
    ) -> SubagentResult:
        if self.done:
            raise RuntimeError("cannot integrate a subagent after the parent run has ended")
        return await self._coordinator.integrate(subagent_id, verification_commands)


class Windcode:
    """Public asynchronous SDK client and runtime owner."""

    def __init__(
        self,
        config: AppConfig,
        *,
        state_root: Path | None = None,
        credential_store: CredentialStore | None = None,
        workspace: Path | None = None,
    ) -> None:
        self.config = config
        self.credential_store = credential_store or FileCredentialStore()
        self.workspace = (workspace or Path.cwd()).expanduser().resolve()
        self.state_root = self._resolve_state_root(state_root)
        self.transport_registry = TransportRegistry()
        self.tool_registry: ToolRegistry | None = None
        self._default_chain: list[str] = []
        self._handles: set[RunHandle] = set()
        self._entered = False
        self.extension_service: ExtensionService | None = None
        self._client_extensions: RunExtensions | None = None
        self._mcp_tool_catalogs: dict[str, tuple[McpToolDefinition, ...]] = {}
        self._mcp_selected_tools: set[str] = set()
        self._mcp_direct_servers: tuple[str, ...] = ()
        self._mcp_start_task: asyncio.Task[None] | None = None
        self.memory_service: MemoryService | None = None

    def _resolve_state_root(self, explicit_root: Path | None) -> Path:
        if explicit_root is not None:
            return explicit_root.expanduser().resolve()
        legacy_root = user_state_path("windcode").expanduser().resolve()
        configured_user_root = self.config.storage.user_storage_root
        user_root = (
            self._configured_state_path(configured_user_root)
            if configured_user_root is not None
            else legacy_root / "state"
        )
        source_root = user_root if user_root.exists() else legacy_root
        configured = self.config.storage.project_state_root
        if configured is None:
            self._migrate_state_root(source_root, user_root)
            return user_root
        project_root = self._configured_state_path(configured)
        self._migrate_state_root(source_root, project_root)
        return project_root

    def _configured_state_path(self, value: str) -> Path:
        project_root = Path(value).expanduser()
        if not project_root.is_absolute():
            project_root = self.workspace / project_root
        return project_root.resolve()

    @staticmethod
    def _state_manifest(root: Path) -> dict[str, int]:
        return {
            str(path.relative_to(root)): path.stat().st_size
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    @classmethod
    def _migrate_state_root(cls, source: Path, target: Path) -> None:
        """Copy the complete legacy state once, validate it, then atomically install it."""
        if source == target or target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_parent = source.parent if target.is_relative_to(source) else target.parent
        temporary = temporary_parent / f".{target.name}.migrate-{uuid4().hex}"
        try:
            if source.exists():
                shutil.copytree(source, temporary, copy_function=shutil.copy2)
                if cls._state_manifest(source) != cls._state_manifest(temporary):
                    raise OSError("project state migration validation failed")
            else:
                temporary.mkdir(parents=True)
            temporary.replace(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    @classmethod
    def open(
        cls,
        config: AppConfig | Mapping[str, Any] | None = None,
        *,
        state_root: Path | None = None,
        credential_store: CredentialStore | None = None,
        workspace: Path | None = None,
    ) -> Self:
        parsed = config if isinstance(config, AppConfig) else AppConfig.model_validate(config or {})
        return cls(
            parsed,
            state_root=state_root,
            credential_store=credential_store,
            workspace=workspace,
        )

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("Windcode client is already open")
        self._entered = True
        self.state_root.mkdir(parents=True, exist_ok=True)
        if self.config.memory.enabled:
            self.memory_service = MemoryService(self.state_root, self.workspace)
        if self.config.providers:
            self.transport_registry = TransportRegistry.from_config(
                self.config,
                credential_store=self.credential_store,
                allow_missing=True,
            )
            if self.config.primary_provider is not None:
                self._default_chain = [
                    alias
                    for alias in (self.config.primary_provider, *self.config.fallback_chain)
                    if alias in self.transport_registry.aliases
                ]
        self.tool_registry = create_builtin_registry(
            shell_timeout=self.config.budgets.shell_timeout_seconds,
        )
        extension_root = self.state_root / "extensions"
        self.extension_service = ExtensionService(
            self.config.extensions,
            self.workspace,
            ExtensionStateStore(extension_root / "state.json"),
            extension_root / "plugins",
        )
        await self.extension_service.reload()
        self._client_extensions = RunExtensions.create(
            self.extension_service.snapshot,
            session_id="client",
            run_id="startup",
            credential_store=self.credential_store,
            max_content_bytes=self.config.extensions.max_content_bytes,
            connect_timeout=self.config.extensions.connect_timeout_seconds,
            call_timeout=self.config.extensions.call_timeout_seconds,
            network_enabled=self.config.sandbox.network_enabled,
            mcp_tool_catalogs=self._mcp_tool_catalogs,
        )
        self._mcp_start_task = asyncio.create_task(self._start_required_mcp())
        return self

    async def _start_required_mcp(self) -> None:
        if self._client_extensions is None or self.tool_registry is None:
            return
        await self._client_extensions.mcp.activate_required()
        registered = await self._client_extensions.mcp_capabilities.register_direct_tools(
            self.tool_registry,
            direct_tool_limit=self.config.extensions.direct_tool_limit,
        )
        if registered:
            self._mcp_direct_servers = self._client_extensions.mcp.required_server_ids

    async def wait_for_required_mcp(self) -> None:
        """Wait for the single client-level MCP startup task."""
        if self._mcp_start_task is not None:
            await self._mcp_start_task

    @property
    def required_mcp_loading(self) -> bool:
        return self._mcp_start_task is not None and not self._mcp_start_task.done()

    def _extensions(self) -> ExtensionService:
        if not self._entered or self.extension_service is None:
            raise RuntimeError("manage extensions inside the Windcode async context")
        return self.extension_service

    @property
    def extension_snapshot(self) -> ExtensionSnapshot:
        return self._extensions().snapshot

    async def list_extensions(self) -> tuple[CapabilityRecord, ...]:
        return await self._extensions().list_capabilities()

    async def inspect_extension(self, identifier: str) -> tuple[CapabilityRecord, ...]:
        return await self._extensions().inspect(identifier)

    async def install_extension(self, path: Path, *, enable: bool = False) -> InstallResult:
        return await self._extensions().install_local(path, enable=enable)

    async def set_extension_enabled(self, identifier: str, enabled: bool) -> ManagementResult:
        return await self._extensions().set_enabled(identifier, enabled)

    async def trust_extension_workspace(
        self, workspace: Path, trusted: bool = True
    ) -> ManagementResult:
        return await self._extensions().trust_workspace(workspace, trusted)

    async def reload_extensions(self) -> ManagementResult:
        result = await self._extensions().reload()
        self._mcp_tool_catalogs.clear()
        self._mcp_selected_tools.clear()
        self._mcp_direct_servers = ()
        return result

    def extension_commands(
        self, *, reserved: frozenset[str] = frozenset()
    ) -> tuple[CommandRoute, ...]:
        return self._extensions().command_routes(reserved=reserved)

    def search_skills(self, query: str = "") -> tuple[SkillSearchResult, ...]:
        """Return enabled, trusted, unshadowed Skills from the current snapshot."""
        catalog = SkillCatalog(
            self.extension_snapshot,
            SkillLoader(max_content_bytes=self.config.extensions.max_content_bytes),
        )
        return catalog.search(query)

    def extension_audit(self) -> tuple[ManagementAuditRecord, ...]:
        return self._extensions().audit_records

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    def register_tool(self, tool: Tool, *, replace_existing: bool = False) -> None:
        if self.tool_registry is None:
            raise RuntimeError("register tools inside the Windcode async context")
        self.tool_registry.register(tool, replace=replace_existing)

    def register_transport(
        self,
        alias: str,
        model: str,
        transport: ModelTransport,
        *,
        replace_existing: bool = False,
        primary: bool = False,
    ) -> None:
        self.transport_registry.register(alias, model, transport, replace=replace_existing)
        if primary or not self._default_chain:
            self._default_chain = [alias]

    async def reconfigure_models(self, config: AppConfig, *, config_file: Path) -> None:
        if any(not handle.done for handle in self._handles):
            raise RuntimeError("cannot configure models while a run is active")
        registry = (
            TransportRegistry.from_config(
                config,
                credential_store=self.credential_store,
                allow_missing=True,
            )
            if config.providers
            else TransportRegistry()
        )
        try:
            save_model_config(config_file, self.config, config)
        except Exception:
            await registry.aclose()
            raise

        previous_registry = self.transport_registry
        self.transport_registry = registry
        self.config = config
        configured_chain = (
            (config.primary_provider, *config.fallback_chain)
            if config.primary_provider is not None
            else ()
        )
        self._default_chain = [
            alias for alias in configured_chain if alias in self.transport_registry.aliases
        ]
        await previous_registry.aclose()

    def _model_chain(self, requested: str | None) -> tuple[ModelTarget, ...]:
        if requested is not None and requested in self.transport_registry.aliases:
            return (self.transport_registry.get(requested),)
        if not self._default_chain:
            raise RuntimeError("no model transport is configured")
        chain = tuple(self.transport_registry.get(alias) for alias in self._default_chain)
        if requested is not None:
            chain = (replace(chain[0], model=requested), *chain[1:])
        return chain

    def _memory(self) -> MemoryService:
        if not self.config.memory.enabled or self.memory_service is None:
            raise RuntimeError("long-term memory is disabled")
        return self.memory_service

    def list_memories(self, *, status: MemoryStatus | None = None) -> tuple[MemoryRecord, ...]:
        service = self._memory()
        return service.store.list(status=status, project_id=service.project_id)

    def search_memories(self, query: str, *, limit: int | None = None) -> tuple[MemoryRecord, ...]:
        service = self._memory()
        results = service.store.search(
            query,
            project_id=service.project_id,
            limit=limit or self.config.memory.recall_limit,
            statuses=(MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE),
        )
        return tuple(result.record for result in results)

    def get_memory(self, memory_id: str) -> MemoryRecord:
        return self._memory().store.get(memory_id)

    def create_memory_candidate(
        self,
        *,
        kind: MemoryKind,
        scope: MemoryScope,
        title: str,
        summary: str,
        body: str,
        source: MemorySource | None = None,
        tags: tuple[str, ...] = (),
        evidence: tuple[str, ...] = (),
        confidence: float = 0.5,
        activation: MemoryActivation | None = None,
        priority: int | None = None,
    ) -> MemoryRecord:
        return self._memory().create_candidate(
            kind=kind,
            scope=scope,
            title=title,
            summary=summary,
            body=body,
            source=source,
            tags=tags,
            evidence=evidence,
            confidence=confidence,
            activation=activation,
            priority=priority,
        )

    def confirm_memory(self, memory_id: str) -> MemoryRecord:
        return self._memory().store.transition(memory_id, MemoryStatus.ACTIVE)

    def reject_memory(self, memory_id: str) -> MemoryRecord:
        return self._memory().store.transition(memory_id, MemoryStatus.REJECTED)

    def archive_memory(self, memory_id: str) -> MemoryRecord:
        return self._memory().store.transition(memory_id, MemoryStatus.ARCHIVED)

    def update_memory(self, memory_id: str, **changes: Any) -> MemoryRecord:
        return self._memory().store.update(memory_id, **changes)

    def set_memory_activation(
        self, memory_id: str, activation: MemoryActivation | str
    ) -> MemoryRecord:
        value = (
            activation if isinstance(activation, MemoryActivation) else MemoryActivation(activation)
        )
        return self._memory().store.update(memory_id, activation=value)

    def delete_memory(self, memory_id: str) -> None:
        self._memory().store.delete(memory_id)

    def rebuild_memory_index(self) -> int:
        return self._memory().store.rebuild()

    def export_project_memories(self, destination: Path) -> tuple[Path, ...]:
        service = self._memory()
        return service.store.export_project(service.project_id, destination)

    def draft_skill_from_memory(self, memory_id: str) -> str:
        return self._memory().draft_skill(memory_id)

    def set_memory_enabled(self, enabled: bool, *, config_file: Path) -> None:
        updated_memory = self.config.memory.model_copy(update={"enabled": enabled})
        updated = self.config.model_copy(update={"memory": updated_memory})
        save_memory_config(config_file, updated)
        self.config = updated
        self.memory_service = MemoryService(self.state_root, self.workspace) if enabled else None

    @staticmethod
    def _session_summary(prompt: str, *, limit: int = 60) -> str:
        summary = " ".join(prompt.split())
        if len(summary) <= limit:
            return summary
        return summary[: limit - 3].rstrip() + "..."

    def _session_store(self, session_id: str) -> SessionStore:
        return SessionStore.open(self.state_root / "sessions", session_id)

    def session_exists(self, session_id: str) -> bool:
        return (self.state_root / "sessions" / session_id / "meta.json").is_file()

    def load_session_records(self, session_id: str) -> tuple[EventRecord, ...]:
        store = self._session_store(session_id)
        if store.metadata.head_record_id is None:
            return ()
        return ancestor_chain(store.load_records(), store.metadata.head_record_id)

    def load_session_messages(self, session_id: str) -> tuple[Message, ...]:
        return heal_dangling_tool_calls(
            tuple(
                message_from_dict(record.payload)
                for record in self.load_session_records(session_id)
                if record.record_type == "conversation_message"
            )
        )

    def _ensure_session_summary(self, store: SessionStore) -> SessionMetadata:
        if store.metadata.summary:
            return store.metadata
        for message in self.load_session_messages(store.metadata.session_id):
            if message.role is not Role.USER:
                continue
            text = "".join(
                block.text for block in message.content if isinstance(block, TextBlock)
            ).strip()
            if text:
                store.set_summary(self._session_summary(text))
                break
        return store.metadata

    def start_run(self, request: RunRequest) -> RunHandle:
        if not self._entered or self.tool_registry is None:
            raise RuntimeError("start runs inside the Windcode async context")
        workspace = request.workspace.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {workspace}")
        sessions_root = self.state_root / "sessions"
        existing_session = (
            request.session_id is not None
            and (sessions_root / request.session_id / "meta.json").exists()
        )
        if existing_session:
            assert request.session_id is not None
            session = SessionStore.open(sessions_root, request.session_id)
        else:
            session = SessionStore.create(sessions_root, request.session_id)
        if not session.metadata.summary:
            session.set_summary(self._session_summary(request.prompt))
        initial_messages: tuple[Message, ...] = ()
        if existing_session and session.metadata.head_record_id is not None:
            records = ancestor_chain(
                session.load_records(),
                session.metadata.head_record_id,
            )
            initial_messages = heal_dangling_tool_calls(
                tuple(
                    message_from_dict(record.payload)
                    for record in records
                    if record.record_type == "conversation_message"
                )
            )
        run_id = uuid4().hex
        artifact_store = ArtifactStore(session.session_dir)
        extension_snapshot = self._extensions().snapshot
        extension_redactor = DynamicRedactor()
        run_extensions = RunExtensions.create(
            extension_snapshot,
            session_id=session.metadata.session_id,
            run_id=run_id,
            credential_store=self.credential_store,
            max_content_bytes=self.config.extensions.max_content_bytes,
            connect_timeout=self.config.extensions.connect_timeout_seconds,
            call_timeout=self.config.extensions.call_timeout_seconds,
            observe_secret=extension_redactor.register,
            artifact_store=artifact_store,
            network_enabled=self.config.sandbox.network_enabled,
            mcp_runtime=(None if self._client_extensions is None else self._client_extensions.mcp),
            mcp_tool_catalogs=self._mcp_tool_catalogs,
        )
        trace = TraceStore(
            run_id,
            root=self.state_root / "traces",
            enabled=self.config.trace.enabled,
            include_tool_arguments=self.config.trace.include_tool_arguments,
            include_transient_events=self.config.trace.include_transient_events,
            retention_days=self.config.trace.retention_days,
            max_total_mb=self.config.trace.max_total_mb,
        )
        bus = EventBus(session, trace)
        run_extensions.event_observer = lambda event: bus.publish(event, durable=True)
        mode = (
            PermissionMode(request.permission_mode)
            if request.permission_mode is not None
            else self.config.permission.mode
        )
        sandbox_status = detect_bubblewrap()
        sandbox = (
            BubblewrapSandbox(workspace, sandbox_status)
            if self.config.sandbox.enabled and sandbox_status.available
            else None
        )
        run_registry = self.tool_registry.clone()
        register_skill_tools(run_registry, run_extensions.skills, run_extensions.activate_skill)
        register_mcp_status_tool(
            run_registry,
            extension_snapshot.capabilities,
            self._mcp_tool_catalogs,
            self._mcp_selected_tools,
        )
        if run_extensions.mcp.server_ids:
            register_mcp_management_tools(
                run_registry, run_extensions.mcp_capabilities, self._mcp_selected_tools
            )
        run_registry.register(
            ShellTool(
                sandbox=sandbox,
                default_timeout=self.config.budgets.shell_timeout_seconds,
            ),
            replace=True,
        )
        policy = PolicyEngine(
            mode,
            sandbox_enabled=self.config.sandbox.enabled,
            sandbox_available=sandbox_status.available,
        )
        for record in session.load_records():
            if record.record_type != "session_approval":
                continue
            if record.payload.get("workspace") != str(workspace):
                continue
            tool_name = record.payload.get("tool_name")
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
        child_tools = run_registry.clone()
        instructions = load_instructions(workspace, workspace_root=workspace)
        run_memory = (
            MemoryService(self.state_root, workspace) if self.config.memory.enabled else None
        )
        tool_memory_id: str | None = None
        if run_memory is not None:

            async def observe_memory_tool(action: str, details: dict[str, Any]) -> None:
                nonlocal tool_memory_id
                memory_id = details.get("memory_id")
                if action in {"activated", "candidate_created", "already_exists"} and isinstance(
                    memory_id, str
                ):
                    tool_memory_id = memory_id
                await bus.publish(
                    MemoryEvent(
                        event_id=uuid4().hex,
                        session_id=session.metadata.session_id,
                        run_id=run_id,
                        turn=0,
                        action=action,
                        memory_id=memory_id if isinstance(memory_id, str) else None,
                        memory_kind=str(details.get("kind", "")) or None,
                        scope=str(details.get("scope", "")) or None,
                        status=str(details.get("status", "")),
                        details=details,
                    ),
                    durable=True,
                )

            register_memory_tools(
                run_registry,
                run_memory,
                observe_memory_tool,
                max_chars=self.config.memory.recall_max_chars,
                user_prompt=request.prompt,
                source=MemorySource(session.metadata.session_id, run_id),
                enabled_kinds=frozenset(
                    kind
                    for kind, enabled in {
                        MemoryKind.USER_PROFILE: self.config.memory.user_profile_enabled,
                        MemoryKind.PROJECT_KNOWLEDGE: self.config.memory.project_knowledge_enabled,
                        MemoryKind.EXPERIENCE: self.config.memory.experience_enabled,
                        MemoryKind.SOP: self.config.memory.sop_enabled,
                        MemoryKind.REFERENCE: self.config.memory.reference_enabled,
                    }.items()
                    if enabled
                ),
            )
        memory_context = ""
        if run_memory is not None:
            memory_context = run_memory.build_context(
                request.prompt,
                baseline_max_records=self.config.memory.baseline_max_records,
                baseline_max_chars=self.config.memory.baseline_max_chars,
                search_limit=self.config.memory.recall_limit,
                search_max_chars=self.config.memory.recall_max_chars,
            )
        budgets = RunBudgets(
            max_model_steps=self.config.budgets.max_model_steps,
            max_tool_calls=self.config.budgets.max_tool_calls,
            max_runtime_seconds=self.config.budgets.max_runtime_seconds,
        )
        control = RunControl(budgets)
        if request.compact_before_run:
            control.request_compaction()
        factory = ChildRuntimeFactory(
            config=self.config,
            state_root=self.state_root,
            parent_tools=child_tools,
            model_chain=lambda model: self._model_chain(model or request.model),
            extension_snapshot=extension_snapshot,
        )
        coordinator = SubagentCoordinator(
            parent_session_id=session.metadata.session_id,
            parent_run_id=run_id,
            workspace=workspace,
            permission_mode=mode,
            config=self.config.subagents,
            event_bus=bus,
            factory=factory,
            worktrees=WorktreeManager(worktrees_root=self.state_root / "worktrees"),
            verification=VerificationRunner(
                sandbox=sandbox,
                timeout_seconds=self.config.budgets.shell_timeout_seconds,
            ),
            network_enabled=self.config.sandbox.network_enabled,
            event_observer=run_extensions.subagent_lifecycle,
        )
        add_subagent_tools(run_registry, coordinator)

        unavailable_mcp_servers = tuple(
            (
                record.public_name,
                "未信任当前工作区, 需要执行 extensions trust 后 reload",
            )
            for record in extension_snapshot.capabilities
            if record.kind is CapabilityKind.MCP_SERVER and record.enabled and not record.trusted
        )

        def make_system_prompt(
            direct_servers: tuple[str, ...], search_servers: tuple[str, ...]
        ) -> str:
            prompt = build_system_prompt(
                workspace=workspace,
                permission_mode=policy.mode,
                instructions=instructions,
                tools=run_registry,
                delegation_mode=self.config.subagents.mode,
                skills=run_extensions.skills.search(),
                mcp_direct_servers=direct_servers,
                mcp_search_servers=search_servers,
                mcp_unavailable_servers=unavailable_mcp_servers,
                memory_enabled=run_memory is not None,
            )
            if memory_context:
                prompt += f"\n\n{memory_context}"
            return prompt

        # Direct tools are not registered until run start (after activation), so
        # build a provisional prompt now and refine it once we know which servers
        # expose their tools directly versus needing the search/select flow.
        system_prompt = make_system_prompt((), run_extensions.mcp.server_ids)

        def record_session_approval(request: PolicyRequest) -> None:
            session.append(
                "session_approval",
                {
                    "workspace": str(workspace),
                    "tool_name": request.tool_name,
                    "effects": sorted(effect.value for effect in request.effects),
                },
                durable=True,
            )

        scheduler = ToolScheduler(
            run_registry,
            policy,
            before_policy=run_extensions.before_policy,
            permission_observer=run_extensions.permission_requested,
            after_execute=run_extensions.after_execute,
            session_approval_recorder=record_session_approval,
        )

        async def run_hook_command(command: str, origin: str, hook_context: HookContext) -> str:
            del hook_context
            scheduled = ScheduledCall(
                uuid4().hex,
                "shell",
                {"command": command},
                origin=origin,
            )
            results = await scheduler.execute(
                (scheduled,),
                ToolContext(workspace, run_id, lambda: control.cancelled),
            )
            result = results[0].result
            if result.is_error:
                raise RuntimeError(result.output)
            return result.output

        run_extensions.hooks.executor.command_runner = run_hook_command
        model_chain = self._model_chain(request.model)

        async def extract_memories(result: RunResult) -> None:
            if (
                not self.config.memory.enabled
                or not self.config.memory.extraction_enabled
                or run_memory is None
            ):
                return
            enabled_kinds = {
                MemoryKind.USER_PROFILE: self.config.memory.user_profile_enabled,
                MemoryKind.PROJECT_KNOWLEDGE: self.config.memory.project_knowledge_enabled,
                MemoryKind.EXPERIENCE: self.config.memory.experience_enabled,
                MemoryKind.SOP: self.config.memory.sop_enabled,
                MemoryKind.REFERENCE: self.config.memory.reference_enabled,
            }
            explicit_experience_id: str | None = None
            if tool_memory_id is not None:
                tool_memory = run_memory.store.get(tool_memory_id)
                if tool_memory.kind is MemoryKind.EXPERIENCE:
                    explicit_experience_id = tool_memory_id
            intent_kind = classify_memory_intent(request.prompt)
            if tool_memory_id is None and intent_kind is not None and enabled_kinds[intent_kind]:
                project_fact = is_project_fact(request.prompt)
                scope = (
                    MemoryScope.USER
                    if intent_kind is MemoryKind.USER_PROFILE
                    or (intent_kind is MemoryKind.REFERENCE and not project_fact)
                    else MemoryScope.PROJECT
                )
                refined = await refine_memory(
                    model_chain[0],
                    text=request.prompt,
                    kind=intent_kind,
                    max_output_tokens=self.config.memory.extraction_max_output_tokens,
                )
                activation: MemoryActivation | None = None
                if intent_kind is MemoryKind.PROJECT_KNOWLEDGE:
                    core = explicitly_always_project_fact(
                        request.prompt
                    ) or await assess_core_project_fact(
                        model_chain[0],
                        text=request.prompt,
                        max_output_tokens=min(256, self.config.memory.extraction_max_output_tokens),
                    )
                    activation = MemoryActivation.ALWAYS if core else MemoryActivation.MANUAL
                priority = 60 if activation is MemoryActivation.ALWAYS else None
                candidate = run_memory.create_candidate(
                    kind=intent_kind,
                    scope=scope,
                    title=refined.title,
                    summary=refined.summary,
                    body=refined.body,
                    source=MemorySource(session.metadata.session_id, run_id),
                    tags=refined.tags,
                    evidence=(
                        () if intent_kind is MemoryKind.SOP else (f"用户原话: {request.prompt}",)
                    ),
                    confidence=0.8,
                    activation=activation,
                    priority=priority,
                )
                if intent_kind is MemoryKind.SOP:
                    saved = candidate
                    action = "candidate_created"
                    policy = "explicit_sop_candidate"
                else:
                    saved = run_memory.store.transition(candidate.memory_id, MemoryStatus.ACTIVE)
                    if intent_kind is MemoryKind.EXPERIENCE:
                        explicit_experience_id = saved.memory_id
                    action = "activated"
                    policy = "explicit_or_stable_fact"
                await bus.publish(
                    MemoryEvent(
                        event_id=uuid4().hex,
                        session_id=session.metadata.session_id,
                        run_id=run_id,
                        turn=0,
                        action=action,
                        memory_id=saved.memory_id,
                        memory_kind=saved.kind.value,
                        scope=saved.scope.value,
                        status=saved.status.value,
                        details={"policy": policy},
                    ),
                    durable=True,
                )
            if self.config.memory.experience_enabled and should_assess_experience(
                status=result.status,
                changed_files=result.changed_files,
                verification=result.verification,
            ):
                experience_text = (
                    f"用户请求:\n{request.prompt}\n\n"
                    f"变更文件:\n{chr(10).join(result.changed_files)}\n\n"
                    f"任务结果:\n{result.final_text}"
                )[: self.config.memory.extraction_max_chars]
                assessment = await assess_experience(
                    model_chain[0],
                    text=experience_text,
                    evidence=result.verification,
                    max_output_tokens=self.config.memory.extraction_max_output_tokens,
                )
                if not assessment.should_store or assessment.memory is None:
                    return
                refined = assessment.memory
                duplicates = tuple(
                    record
                    for record in run_memory.store.list(
                        status=MemoryStatus.ACTIVE,
                        project_id=run_memory.project_id,
                    )
                    if record.kind is MemoryKind.EXPERIENCE
                    and (
                        record.title.casefold() == refined.title.casefold()
                        or record.summary.casefold() == refined.summary.casefold()
                    )
                )
                if duplicates:
                    existing = duplicates[0]
                    if explicit_experience_id is not None:
                        run_memory.store.delete(explicit_experience_id)
                    evidence = tuple(dict.fromkeys((*existing.evidence, *result.verification)))
                    run_memory.store.update(existing.memory_id, evidence=evidence)
                    run_memory.store.record_outcome(existing.memory_id, success=True)
                    return
                if explicit_experience_id is not None:
                    experience = run_memory.store.update(
                        explicit_experience_id,
                        title=refined.title,
                        summary=refined.summary,
                        body=refined.body,
                        tags=refined.tags,
                        evidence=result.verification,
                        confidence=0.8,
                    )
                else:
                    experience = run_memory.create_candidate(
                        kind=MemoryKind.EXPERIENCE,
                        scope=MemoryScope.PROJECT,
                        title=refined.title,
                        summary=refined.summary,
                        body=refined.body,
                        source=MemorySource(session.metadata.session_id, run_id),
                        tags=refined.tags,
                        evidence=result.verification,
                        confidence=0.7,
                    )
                verified = run_memory.store.transition(experience.memory_id, MemoryStatus.ACTIVE)
                await bus.publish(
                    MemoryEvent(
                        event_id=uuid4().hex,
                        session_id=session.metadata.session_id,
                        run_id=run_id,
                        turn=0,
                        action="activated",
                        memory_id=verified.memory_id,
                        memory_kind=verified.kind.value,
                        scope=verified.scope.value,
                        status=verified.status.value,
                        details={"verified": True, "policy": "no_execution_no_memory"},
                    ),
                    durable=True,
                )
                if self.config.memory.sop_enabled and assessment.sop is not None:
                    sop = assessment.sop
                    sop_candidate = run_memory.create_candidate(
                        kind=MemoryKind.SOP,
                        scope=MemoryScope.PROJECT,
                        title=sop.title,
                        summary=sop.summary,
                        body=sop.body,
                        source=MemorySource(session.metadata.session_id, run_id),
                        tags=sop.tags,
                        evidence=result.verification,
                        confidence=0.7,
                    )
                    await bus.publish(
                        MemoryEvent(
                            event_id=uuid4().hex,
                            session_id=session.metadata.session_id,
                            run_id=run_id,
                            turn=0,
                            action="candidate_created",
                            memory_id=sop_candidate.memory_id,
                            memory_kind=sop_candidate.kind.value,
                            scope=sop_candidate.scope.value,
                            status=sop_candidate.status.value,
                            details={"verified": True, "policy": "experience_sop_candidate"},
                        ),
                        durable=True,
                    )

        loop = AgentLoop(
            session_id=session.metadata.session_id,
            run_id=run_id,
            model_chain=model_chain,
            scheduler=scheduler,
            control=control,
            event_bus=bus,
            system_prompt=system_prompt,
            token_estimator=TokenEstimator(
                self.config.context.window_tokens,
                compaction_threshold=self.config.context.compaction_threshold,
            ),
            artifact_store=artifact_store,
            preserve_recent_turns=self.config.context.preserve_recent_turns,
            max_tool_result_chars=self.config.context.max_tool_result_chars,
            close_event_bus=False,
            sourced_context_provider=run_extensions.drain_context,
            compact_observer=run_extensions.compact_lifecycle,
            completion_observer=extract_memories,
        )
        after_sequence = session.metadata.next_sequence - 1

        async def run_with_subagents() -> RunResult:
            try:
                await self.wait_for_required_mcp()
                await run_extensions.mcp_capabilities.register_direct_tools(
                    run_registry,
                    direct_tool_limit=self.config.extensions.direct_tool_limit,
                )
                await run_extensions.mcp_capabilities.register_direct_tools(
                    child_tools,
                    direct_tool_limit=self.config.extensions.direct_tool_limit,
                )
                await run_extensions.mcp_capabilities.register_selected_tools(
                    run_registry, self._mcp_selected_tools
                )
                await run_extensions.mcp_capabilities.register_selected_tools(
                    child_tools, self._mcp_selected_tools
                )
                direct_servers = self._mcp_direct_servers
                search_servers = tuple(
                    server_id
                    for server_id in run_extensions.mcp.server_ids
                    if server_id not in set(direct_servers)
                )
                loop.system_prompt = make_system_prompt(direct_servers, search_servers)
                if memory_context:
                    await bus.publish(
                        MemoryEvent(
                            event_id=uuid4().hex,
                            session_id=session.metadata.session_id,
                            run_id=run_id,
                            turn=0,
                            action="recalled",
                            status="active",
                            details={"characters": len(memory_context)},
                        )
                    )
                if not existing_session:
                    await run_extensions.lifecycle(HookEvent.SESSION_START)
                await run_extensions.lifecycle(HookEvent.USER_SUBMIT)
                await run_extensions.lifecycle(HookEvent.RUN_START)
                prompt_parts = request.prompt.strip().split(maxsplit=1)
                if prompt_parts and prompt_parts[0].startswith("$"):
                    await run_extensions.activate_skill(prompt_parts[0])
                elif prompt_parts and prompt_parts[0].startswith("@prompt:"):
                    await run_extensions.activate_prompt(prompt_parts[0].removeprefix("@prompt:"))
                elif prompt_parts and prompt_parts[0].startswith("@capability:"):
                    run_extensions.activate_capability(prompt_parts[0].removeprefix("@capability:"))
                if existing_session:
                    await coordinator.recover()
                result = await loop.run(request.prompt, workspace, initial_messages)
                await run_extensions.lifecycle(HookEvent.RUN_END, status=result.status)
                return result
            except BaseException:
                await run_extensions.lifecycle(HookEvent.RUN_ERROR, status="error")
                raise
            finally:
                await coordinator.shutdown("parent run ended")
                await run_extensions.lifecycle(HookEvent.SESSION_END)
                await run_extensions.aclose()
                # The MCP runtime outlives this run; do not retain its closed event bus.
                run_extensions.mcp.observer = None
                extension_redactor.clear()
                await bus.close()

        task = asyncio.create_task(run_with_subagents())
        handle = RunHandle(
            task,
            bus,
            control,
            after_sequence=after_sequence,
            coordinator=coordinator,
            policy=policy,
            loop=loop,
        )
        self._handles.add(handle)
        task.add_done_callback(lambda _task: self._handles.discard(handle))
        return handle

    def list_sessions(self) -> tuple[SessionMetadata, ...]:
        sessions_root = self.state_root / "sessions"
        if not sessions_root.exists():
            return ()
        sessions: list[SessionMetadata] = []
        for path in sessions_root.iterdir():
            if not path.is_dir() or not (path / "meta.json").is_file():
                continue
            store = SessionStore.open(sessions_root, path.name)
            sessions.append(self._ensure_session_summary(store))
        return tuple(sorted(sessions, key=lambda item: item.updated_at, reverse=True))

    def rewind_session(self, session_id: str, record_id: str) -> EventRecord:
        store = SessionStore.open(self.state_root / "sessions", session_id)
        return create_branch(
            store,
            record_id,
            "branch_point",
            {"source_record_id": record_id},
        )

    async def aclose(self) -> None:
        if not self._entered:
            return
        handles = tuple(self._handles)
        await asyncio.gather(*(handle.cancel() for handle in handles))
        if self._mcp_start_task is not None:
            if not self._mcp_start_task.done():
                self._mcp_start_task.cancel()
            await asyncio.gather(self._mcp_start_task, return_exceptions=True)
            self._mcp_start_task = None
        if self._client_extensions is not None:
            self._client_extensions.mcp.observer = None
        extension_close = (
            self._client_extensions.aclose()
            if self._client_extensions is not None
            else asyncio.sleep(0)
        )
        try:
            await asyncio.gather(
                extension_close,
                self.transport_registry.aclose(),
                return_exceptions=True,
            )
        finally:
            self._client_extensions = None
            self._entered = False


__all__ = ["RunHandle", "Windcode"]
