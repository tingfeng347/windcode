from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from windcode.application import (
    ConfigurationApplication,
    ExtensionApplication,
    McpStartupStatus,
    ProviderApplication,
)
from windcode.auth import CredentialStore, FileCredentialStore
from windcode.config import AppConfig
from windcode.domain.events import RunRequest
from windcode.domain.messages import (
    Message,
    Role,
    TextBlock,
    heal_dangling_tool_calls,
    message_from_dict,
)
from windcode.domain.tools import Tool
from windcode.extensions.commands import CommandRoute
from windcode.extensions.models import (
    CapabilityRecord,
    ExtensionSnapshot,
    ManagementResult,
)
from windcode.extensions.plugins.installer import InstallResult
from windcode.extensions.service import ExtensionService
from windcode.extensions.skills.tools import SkillSearchResult
from windcode.extensions.state import ManagementAuditRecord
from windcode.memory import (
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
)
from windcode.providers import (
    ModelTransport,
    TransportRegistry,
)
from windcode.runtime.parent_run import RunExtensionState
from windcode.runtime.run_builder import RunBuilder
from windcode.runtime.run_handle import RunHandle
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
    create_builtin_registry,
)


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
        self.credential_store = credential_store or FileCredentialStore()
        self._configuration = ConfigurationApplication(config)
        self._providers = ProviderApplication(self._configuration, self.credential_store)
        self.workspace = (workspace or Path.cwd()).expanduser().resolve()
        self.state_root = self._resolve_state_root(state_root)
        self._extension_application = ExtensionApplication(
            self._configuration,
            self.credential_store,
            workspace=self.workspace,
            state_root=self.state_root,
            user_skill_root=self._user_storage_root() / "skills",
        )
        self.tool_registry: ToolRegistry | None = None
        self._handles: set[RunHandle] = set()
        self._entered = False
        self._closing = False
        self.memory_service: MemoryService | None = None

    @property
    def config(self) -> AppConfig:
        return self._configuration.current

    @config.setter
    def config(self, value: AppConfig) -> None:
        self._configuration.current = value

    @property
    def transport_registry(self) -> TransportRegistry:
        return self._providers.registry

    @transport_registry.setter
    def transport_registry(self, value: TransportRegistry) -> None:
        self._providers.registry = value

    @property
    def model_startup_error(self) -> str | None:
        return self._providers.startup_error

    @model_startup_error.setter
    def model_startup_error(self, value: str | None) -> None:
        self._providers.startup_error = value

    @property
    def extension_service(self) -> ExtensionService | None:
        return self._extension_application.service

    @extension_service.setter
    def extension_service(self, value: ExtensionService | None) -> None:
        self._extension_application.service = value

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
        if self._entered or self._closing:
            raise RuntimeError("Windcode client is already open")
        self._entered = True
        self.state_root.mkdir(parents=True, exist_ok=True)
        if self.config.memory.enabled:
            self.memory_service = MemoryService(self.state_root, self.workspace)
        await self._providers.open()
        self.tool_registry = create_builtin_registry(
            shell_timeout=self.config.budgets.shell_timeout_seconds,
        )
        await self._extension_application.open()
        return self

    async def wait_for_required_mcp(self) -> None:
        """Wait for the single client-level MCP startup task."""
        await self._extension_application.wait_required()

    @property
    def required_mcp_loading(self) -> bool:
        return self._extension_application.required_loading

    @property
    def mcp_startup_status(self) -> McpStartupStatus:
        return self._extension_application.startup_status

    @property
    def extension_snapshot(self) -> ExtensionSnapshot:
        return self._extension_application.snapshot

    async def list_extensions(self) -> tuple[CapabilityRecord, ...]:
        return await self._extension_application.list_capabilities()

    async def inspect_extension(self, identifier: str) -> tuple[CapabilityRecord, ...]:
        return await self._extension_application.inspect(identifier)

    async def install_extension(self, path: Path, *, enable: bool = False) -> InstallResult:
        return await self._extension_application.install_local(path, enable=enable)

    async def set_extension_enabled(self, identifier: str, enabled: bool) -> ManagementResult:
        return await self._extension_application.set_enabled(identifier, enabled)

    async def trust_extension_workspace(
        self, workspace: Path, trusted: bool = True
    ) -> ManagementResult:
        return await self._extension_application.trust_workspace(workspace, trusted)

    async def reload_extensions(self) -> ManagementResult:
        return await self._extension_application.reload()

    def extension_commands(
        self, *, reserved: frozenset[str] = frozenset()
    ) -> tuple[CommandRoute, ...]:
        return self._extension_application.command_routes(reserved=reserved)

    def search_skills(self, query: str = "") -> tuple[SkillSearchResult, ...]:
        """Return enabled, trusted, unshadowed Skills from the current snapshot."""
        return self._extension_application.search_skills(query)

    def extension_audit(self) -> tuple[ManagementAuditRecord, ...]:
        return self._extension_application.audit_records

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
        self._providers.register(
            alias,
            model,
            transport,
            replace_existing=replace_existing,
            primary=primary,
        )

    async def reconfigure_models(self, config: AppConfig, *, config_file: Path) -> None:
        if any(not handle.done for handle in self._handles):
            raise RuntimeError("cannot configure models while a run is active")
        await self._providers.reconfigure(config, config_file=config_file)

    def can_resolve_model(self, requested: str | None = None) -> bool:
        return self._providers.can_resolve(requested)

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
        self._configuration.set_memory_enabled(enabled, config_file=config_file)
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
        if not self._accepting_runs():
            raise RuntimeError("start runs inside the Windcode async context")
        handle = self._extension_application.bind_run(
            lambda extensions: self._run_builder(extensions).start(request)
        )
        self._handles.add(handle)
        handle.add_done_callback(self._handles.discard)
        return handle

    def _accepting_runs(self) -> bool:
        return self._entered and not self._closing and self.tool_registry is not None

    def _run_builder(self, extensions: RunExtensionState) -> RunBuilder:
        if self.tool_registry is None:
            raise RuntimeError("run builder requires an initialized tool registry")
        return RunBuilder(
            self.config,
            state_root=self.state_root,
            user_storage_root=self._user_storage_root(),
            base_tools=self.tool_registry,
            model_chain=self._providers.resolve,
            extensions=extensions,
        )

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
        if not self._entered or self._closing:
            return
        self._closing = True
        try:
            handles = tuple(self._handles)
            await asyncio.gather(*(handle.cancel() for handle in handles))
            await asyncio.gather(
                self._extension_application.aclose(),
                self._providers.aclose(),
                return_exceptions=True,
            )
        except BaseException:
            self._closing = False
            raise
        else:
            self._entered = False
            self._closing = False


__all__ = ["RunHandle", "Windcode"]
