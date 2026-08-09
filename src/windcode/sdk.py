from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from uuid import uuid4

from windcode.auth import CredentialStore, CredentialStoreError, FileCredentialStore
from windcode.config import (
    AppConfig,
    save_memory_config,
    save_model_config,
)
from windcode.domain.errors import RequiredExtensionError, RequiredExtensionStartupError
from windcode.domain.events import RunRequest, RunResult
from windcode.domain.messages import (
    Message,
    Role,
    TextBlock,
    heal_dangling_tool_calls,
    message_from_dict,
)
from windcode.domain.tools import Tool, ToolContext
from windcode.extensions.commands import CommandRoute
from windcode.extensions.hooks.models import HookContext, HookEvent
from windcode.extensions.mcp import McpServerState
from windcode.extensions.mcp.catalog import McpToolDefinition
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
)
from windcode.extensions.state import ExtensionStateStore, ManagementAuditRecord
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
)
from windcode.observability import DynamicRedactor
from windcode.policy import PolicyRequest
from windcode.providers import (
    ModelTarget,
    ModelTransport,
    ProviderConfigurationError,
    TransportRegistry,
)
from windcode.runtime.control import RunBudgets, RunControl
from windcode.runtime.loop import (
    AgentLoop,
    ContextWindow,
    ModelSession,
    RunIdentity,
    RunJournal,
    RunObservers,
    ToolRuntime,
)
from windcode.runtime.prompts import build_system_prompt
from windcode.runtime.run_builder import RunBuilder
from windcode.runtime.run_handle import RunHandle
from windcode.runtime.run_memory import RunMemory
from windcode.runtime.scheduler import ScheduledCall, ToolScheduler
from windcode.runtime.subagents import (
    SubagentCoordinator,
    VerificationRunner,
)
from windcode.sandbox import SandboxPreset, create_sandbox_backend
from windcode.sessions import (
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
)
from windcode.worktrees import WorktreeManager


@dataclass(frozen=True, slots=True)
class McpStartupStatus:
    total: int = 0
    loaded: int = 0
    failed_servers: tuple[str, ...] = ()
    lazy: int = 0


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
        self.model_startup_error: str | None = None
        self.tool_registry: ToolRegistry | None = None
        self._default_chain: list[str] = []
        self._handles: set[RunHandle] = set()
        self._entered = False
        self.extension_service: ExtensionService | None = None
        self._client_extensions: RunExtensions | None = None
        self._mcp_tool_catalogs: dict[str, tuple[McpToolDefinition, ...]] = {}
        self._mcp_selected_tools: set[str] = set()
        self._mcp_start_task: asyncio.Task[None] | None = None
        self._mcp_retirement_tasks: set[asyncio.Task[None]] = set()
        self.memory_service: MemoryService | None = None

    def _resolve_state_root(self, explicit_root: Path | None) -> Path:
        if explicit_root is not None:
            return explicit_root.expanduser().resolve()
        configured_project_root = self.config.storage.project_state_root
        if configured_project_root is not None:
            return self._configured_state_path(configured_project_root)
        configured_user_root = self.config.storage.user_storage_root
        return self._configured_state_path(configured_user_root)

    def _configured_state_path(self, value: str) -> Path:
        project_root = Path(value).expanduser()
        if not project_root.is_absolute():
            project_root = self.workspace / project_root
        return project_root.resolve()

    def _user_storage_root(self) -> Path:
        configured = self.config.storage.user_storage_root
        return self._configured_state_path(configured)

    def sandbox_status(self, workspace: Path | None = None) -> str:
        selected_workspace = (workspace or self.workspace).expanduser().resolve()
        preset = SandboxPreset(self.config.sandbox.preset)
        backend, _ = create_sandbox_backend(selected_workspace, preset=preset)
        if backend is None:
            return f"none/{preset.value}"
        return f"{backend.status.backend}/{preset.value}/{backend.status.state.value}"

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
            try:
                self.transport_registry = TransportRegistry.from_config(
                    self.config,
                    credential_store=self.credential_store,
                    allow_missing=True,
                )
            except (CredentialStoreError, ProviderConfigurationError) as exc:
                self.transport_registry = TransportRegistry()
                self.model_startup_error = str(exc)
            else:
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
            user_skill_root=self._user_storage_root() / "skills",
        )
        await self.extension_service.reload()
        self._client_extensions = self._create_client_extensions()
        self._mcp_start_task = asyncio.create_task(
            self._start_required_mcp(self._client_extensions)
        )
        return self

    def _create_client_extensions(self) -> RunExtensions:
        if self.extension_service is None:
            raise RuntimeError("extension service is not initialized")
        return RunExtensions.create(
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

    async def _retire_client_extensions(
        self,
        extensions: RunExtensions,
        handles: tuple[RunHandle, ...],
        startup: asyncio.Task[None] | None,
    ) -> None:
        await asyncio.gather(*(handle.result() for handle in handles), return_exceptions=True)
        if startup is not None:
            if not startup.done():
                startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
        extensions.mcp.observer = None
        await extensions.aclose()

    async def _start_required_mcp(self, extensions: RunExtensions) -> None:
        try:
            ready_required_servers = await extensions.mcp.activate_required()
            await extensions.mcp_capabilities.register_direct_tools(
                ToolRegistry(),
                direct_tool_limit=self.config.extensions.direct_tool_limit,
                server_ids=ready_required_servers,
                strict=True,
            )
        except RequiredExtensionError:
            raise
        except Exception as exc:
            raise RequiredExtensionStartupError(
                extensions.mcp.required_server_ids,
                extension_kind="MCP",
            ) from exc

    async def wait_for_required_mcp(self) -> None:
        """Wait for the single client-level MCP startup task."""
        if self._mcp_start_task is not None:
            await asyncio.shield(self._mcp_start_task)

    @property
    def required_mcp_loading(self) -> bool:
        return self._mcp_start_task is not None and not self._mcp_start_task.done()

    @property
    def mcp_startup_status(self) -> McpStartupStatus:
        if self._client_extensions is None:
            return McpStartupStatus()
        runtime = self._client_extensions.mcp
        loaded = len(runtime.ready_server_ids)
        failed = runtime.failed_server_ids
        lazy = sum(
            runtime.state(server_id) is McpServerState.DISCOVERED
            for server_id in runtime.server_ids
        )
        return McpStartupStatus(len(runtime.server_ids), loaded, failed, lazy)

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
        previous = self._client_extensions
        previous_startup = self._mcp_start_task
        active_handles = tuple(handle for handle in self._handles if not handle.done)
        self._mcp_tool_catalogs = {}
        self._mcp_selected_tools = set()
        self._client_extensions = self._create_client_extensions()
        self._mcp_start_task = asyncio.create_task(
            self._start_required_mcp(self._client_extensions)
        )
        if previous is not None:
            retirement = asyncio.create_task(
                self._retire_client_extensions(previous, active_handles, previous_startup)
            )
            self._mcp_retirement_tasks.add(retirement)
            retirement.add_done_callback(self._mcp_retirement_tasks.discard)
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
        self.model_startup_error = None
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
        if config.primary_provider is not None and config.primary_provider not in registry.aliases:
            await registry.aclose()
            raise ProviderConfigurationError(
                f"primary provider {config.primary_provider!r} is not runnable; "
                "configure its credential or choose a connected provider"
            )
        try:
            save_model_config(config_file, self.config, config)
        except Exception:
            await registry.aclose()
            raise

        previous_registry = self.transport_registry
        self.transport_registry = registry
        self.model_startup_error = None
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
            raise ProviderConfigurationError("no runnable model provider is configured")
        chain = tuple(self.transport_registry.get(alias) for alias in self._default_chain)
        if requested is not None:
            chain = (replace(chain[0], model=requested), *chain[1:])
        return chain

    def can_resolve_model(self, requested: str | None = None) -> bool:
        return (requested is not None and requested in self.transport_registry.aliases) or bool(
            self._default_chain
        )

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
        builder = RunBuilder(
            self.config,
            state_root=self.state_root,
            base_tools=self.tool_registry,
            model_chain=self._model_chain,
        )
        preparation = builder.prepare_parent(request)
        workspace = preparation.workspace
        existing_session = preparation.existing_session
        session = preparation.session
        initial_messages = preparation.initial_messages
        run_id = preparation.run_id
        artifact_store = preparation.artifact_store
        extension_snapshot = self._extensions().snapshot
        client_extensions = self._client_extensions
        startup_task = self._mcp_start_task
        mcp_tool_catalogs = self._mcp_tool_catalogs
        mcp_selected_tools = self._mcp_selected_tools
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
            mcp_runtime=(None if client_extensions is None else client_extensions.mcp),
            mcp_tool_catalogs=mcp_tool_catalogs,
        )
        resources = builder.resources(preparation)
        bus = resources.event_bus
        run_extensions.event_observer = lambda event: bus.publish(event, durable=True)
        access = builder.prepare_parent_access(
            preparation,
            request,
            run_extensions,
            extension_snapshot,
            mcp_tool_catalogs,
            mcp_selected_tools,
        )
        mode = access.permission_mode
        sandbox = access.sandbox
        sandbox_policy = access.sandbox_policy
        run_registry = access.registry
        policy = access.policy
        child_tools = access.child_tools
        instructions = access.instructions
        model_chain = self._model_chain(request.model)
        run_memory = RunMemory(
            self.config.memory,
            state_root=self.state_root,
            workspace=workspace,
            request=request,
            identity=RunIdentity(session.metadata.session_id, run_id),
            registry=run_registry,
            event_bus=bus,
            model=model_chain[0],
        )
        budgets = RunBudgets(
            max_model_steps=self.config.budgets.max_model_steps,
            max_tool_calls=self.config.budgets.max_tool_calls,
            max_runtime_seconds=self.config.budgets.max_runtime_seconds,
        )
        control = RunControl(budgets)
        if request.compact_before_run:
            control.request_compaction()
        child_scope = builder.child_scope(
            child_tools,
            extension_snapshot,
            default_model=request.model,
        )
        coordinator = SubagentCoordinator(
            parent_session_id=session.metadata.session_id,
            parent_run_id=run_id,
            workspace=workspace,
            permission_mode=mode,
            config=self.config.subagents,
            event_bus=bus,
            factory=child_scope,
            worktrees=WorktreeManager(
                worktrees_root=self.state_root / "worktrees",
                fallback_worktrees_root=self._user_storage_root() / "worktrees",
            ),
            verification=VerificationRunner(
                sandbox=sandbox,
                sandbox_policy=sandbox_policy,
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
            startup_unavailable = tuple(
                (server_id, "启动连接失败, 本轮已降级且不会阻断普通消息")
                for server_id in self.mcp_startup_status.failed_servers
            )
            prompt = build_system_prompt(
                workspace=workspace,
                permission_mode=policy.mode,
                instructions=instructions,
                tools=run_registry,
                delegation_mode=self.config.subagents.mode,
                skills=run_extensions.skills.search(),
                mcp_direct_servers=direct_servers,
                mcp_search_servers=search_servers,
                mcp_unavailable_servers=(*unavailable_mcp_servers, *startup_unavailable),
                memory_enabled=run_memory.enabled,
            )
            if run_memory.context:
                prompt += f"\n\n{run_memory.context}"
            return prompt

        # Direct tools are not registered until run start (after activation), so
        # build a provisional prompt now and refine it once we know which servers
        # expose their tools directly versus needing the search/select flow.
        system_prompt = make_system_prompt((), run_extensions.mcp.server_ids)

        def record_session_approval(request: PolicyRequest) -> None:
            payload: dict[str, object] = {
                "workspace": str(workspace),
                "tool_name": request.tool_name,
            }
            if request.proposed_rule is not None:
                payload["rule"] = request.proposed_rule.model_copy(
                    update={"source": "session"}
                ).model_dump(mode="json")
            else:
                payload["effects"] = sorted(effect.value for effect in request.effects)
            session.append(
                "session_approval",
                payload,
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
        run_end_observed = False

        async def complete_run(result: RunResult) -> None:
            nonlocal run_end_observed
            run_end_observed = True
            await run_extensions.lifecycle(HookEvent.RUN_END, status=result.status)
            await run_memory.complete(result)

        loop = AgentLoop(
            identity=RunIdentity(session.metadata.session_id, run_id),
            model=ModelSession(
                model_chain,
                system_prompt,
                stream_idle_timeout_seconds=(self.config.budgets.model_stream_idle_timeout_seconds),
            ),
            tools=ToolRuntime(scheduler, control),
            journal=RunJournal(bus, close_on_exit=False),
            context=ContextWindow(
                token_estimator=resources.token_estimator,
                artifact_store=resources.artifact_store,
                preserve_recent_turns=self.config.context.preserve_recent_turns,
                max_tool_result_chars=self.config.context.max_tool_result_chars,
            ),
            observers=RunObservers(
                sourced_context=run_extensions.drain_context,
                compact=run_extensions.compact_lifecycle,
                completion=complete_run,
            ),
        )
        after_sequence = session.metadata.next_sequence - 1

        async def run_with_subagents() -> RunResult:
            observer_token = run_extensions.mcp.bind_observer(run_extensions.observe_mcp)
            try:
                if startup_task is not None:
                    await asyncio.shield(startup_task)
                ready_required_servers = tuple(
                    server_id
                    for server_id in run_extensions.mcp.required_server_ids
                    if run_extensions.mcp.state(server_id) is McpServerState.READY
                )
                direct_tools = await run_extensions.mcp_capabilities.register_direct_tools(
                    run_registry,
                    direct_tool_limit=self.config.extensions.direct_tool_limit,
                    server_ids=ready_required_servers,
                )
                await run_extensions.mcp_capabilities.register_direct_tools(
                    child_tools,
                    direct_tool_limit=self.config.extensions.direct_tool_limit,
                    server_ids=ready_required_servers,
                )
                await run_extensions.mcp_capabilities.register_selected_tools(
                    run_registry, mcp_selected_tools
                )
                await run_extensions.mcp_capabilities.register_selected_tools(
                    child_tools, mcp_selected_tools
                )
                direct_servers = ready_required_servers if direct_tools else ()
                search_servers = tuple(
                    server_id
                    for server_id in run_extensions.mcp.server_ids
                    if server_id not in set(direct_servers)
                )
                loop.system_prompt = make_system_prompt(direct_servers, search_servers)
                await run_memory.publish_recalled()
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
                if not run_end_observed:
                    try:
                        await run_extensions.lifecycle(HookEvent.RUN_END, status=result.status)
                    except RequiredExtensionError:
                        # A primary failed/cancelled terminal record already owns the outcome.
                        pass
                return result
            except RequiredExtensionError as exc:
                await loop.record_startup_failure(exc)
                try:
                    await run_extensions.lifecycle(HookEvent.RUN_ERROR, status="error")
                except BaseException:
                    pass
                raise
            except BaseException:
                try:
                    await run_extensions.lifecycle(HookEvent.RUN_ERROR, status="error")
                except BaseException:
                    pass
                raise
            finally:
                await asyncio.gather(
                    coordinator.shutdown("parent run ended"), return_exceptions=True
                )
                await asyncio.gather(
                    run_extensions.lifecycle(HookEvent.SESSION_END), return_exceptions=True
                )
                await asyncio.gather(run_extensions.aclose(), return_exceptions=True)
                run_extensions.mcp.reset_observer(observer_token)
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

    def rewind_session(
        self,
        session_id: str,
        record_id: str,
        *,
        include_selected: bool = False,
    ) -> EventRecord:
        store = SessionStore.open(self.state_root / "sessions", session_id)
        parent_id = record_id
        if include_selected:
            records = {record.record_id: record for record in store.load_records()}
            try:
                parent_id = records[record_id].parent_id
            except KeyError as exc:
                raise ValueError(f"unknown session record id: {record_id}") from exc
            if parent_id is None:
                return store.append(
                    "branch_point",
                    {"source_record_id": record_id},
                    root=True,
                    durable=True,
                )
        return create_branch(
            store,
            parent_id,
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
        if self._mcp_retirement_tasks:
            await asyncio.gather(*tuple(self._mcp_retirement_tasks), return_exceptions=True)
            self._mcp_retirement_tasks.clear()
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
