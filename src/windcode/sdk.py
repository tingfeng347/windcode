from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from windcode.application import (
    ApplicationLifecycle,
    ConfigurationApplication,
    ExtensionApplication,
    McpStartupStatus,
    MemoryApplication,
    ProviderApplication,
    RunApplication,
    SessionApplication,
)
from windcode.application.contracts import (
    CapabilityRecord,
    CommandRoute,
    EventRecord,
    ExtensionScope,
    ExtensionService,
    ExtensionSnapshot,
    InstallResult,
    ManagementAuditRecord,
    ManagementResult,
    MemoryActivation,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemorySource,
    MemoryStatus,
    Message,
    ModelTransport,
    RunHandle,
    RunRequest,
    SessionMetadata,
    SkillSearchResult,
    Tool,
    ToolRegistry,
    TransportRegistry,
)
from windcode.auth import CredentialStore, FileCredentialStore
from windcode.config import AppConfig
from windcode.sandbox import SandboxPreset, create_sandbox_backend


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
        self._workspace = (workspace or Path.cwd()).expanduser().resolve()
        self._state_root = self._resolve_state_root(state_root)
        self._extension_application = ExtensionApplication(
            self._configuration,
            self.credential_store,
            workspace=self.workspace,
            state_root=self.state_root,
            user_skill_root=self._user_storage_root() / "skills",
        )
        self._runs = RunApplication(
            self._configuration,
            self._providers,
            self._extension_application,
            workspace=self.workspace,
            state_root=self.state_root,
        )
        self._memory_application = MemoryApplication(self._configuration)
        self._sessions = SessionApplication(self.state_root)
        self._lifecycle = ApplicationLifecycle(
            self._configuration,
            self._providers,
            self._extension_application,
            self._runs,
            self._memory_application,
        )

    @property
    def config(self) -> AppConfig:
        return self._configuration.current

    @config.setter
    def config(self, value: AppConfig) -> None:
        self._configuration.current = value

    @property
    def workspace(self) -> Path:
        return self._workspace

    @workspace.setter
    def workspace(self, value: Path) -> None:
        self._workspace = value
        if hasattr(self, "_runs"):
            self._runs.workspace = value

    @property
    def state_root(self) -> Path:
        return self._state_root

    @state_root.setter
    def state_root(self, value: Path) -> None:
        self._state_root = value
        if hasattr(self, "_runs"):
            self._runs.state_root = value
        if hasattr(self, "_sessions"):
            self._sessions.state_root = value

    @property
    def memory_service(self) -> MemoryService | None:
        return self._memory_application.service

    @memory_service.setter
    def memory_service(self, value: MemoryService | None) -> None:
        self._memory_application.service = value

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

    @property
    def tool_registry(self) -> ToolRegistry | None:
        return self._runs.registry

    @tool_registry.setter
    def tool_registry(self, value: ToolRegistry | None) -> None:
        self._runs.registry = value

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
        return self._configuration.user_storage_root(self.workspace)

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
        await self._lifecycle.open(state_root=self.state_root, workspace=self.workspace)
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

    async def trust_extension_capability(
        self,
        identifier: str,
        trusted: bool = True,
        *,
        scope: ExtensionScope | None = None,
    ) -> ManagementResult:
        return await self._extension_application.trust_capability(identifier, trusted, scope=scope)

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
        self._runs.register_tool(tool, replace_existing=replace_existing)

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
        if self._runs.has_active_runs():
            raise RuntimeError("cannot configure models while a run is active")
        await self._providers.reconfigure(config, config_file=config_file)

    def can_resolve_model(self, requested: str | None = None) -> bool:
        return self._providers.can_resolve(requested)

    async def list_memories(
        self, *, status: MemoryStatus | None = None
    ) -> tuple[MemoryRecord, ...]:
        return await self._memory_application.list(status=status)

    async def search_memories(
        self, query: str, *, limit: int | None = None
    ) -> tuple[MemoryRecord, ...]:
        return await self._memory_application.search(query, limit=limit)

    async def get_memory(self, memory_id: str) -> MemoryRecord:
        return await self._memory_application.get(memory_id)

    async def create_memory_candidate(
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
        return await self._memory_application.create_candidate(
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

    async def confirm_memory(self, memory_id: str) -> MemoryRecord:
        return await self._memory_application.transition(memory_id, MemoryStatus.ACTIVE)

    async def reject_memory(self, memory_id: str) -> MemoryRecord:
        return await self._memory_application.transition(memory_id, MemoryStatus.REJECTED)

    async def archive_memory(self, memory_id: str) -> MemoryRecord:
        return await self._memory_application.transition(memory_id, MemoryStatus.ARCHIVED)

    async def update_memory(self, memory_id: str, **changes: Any) -> MemoryRecord:
        return await self._memory_application.update(memory_id, **changes)

    async def set_memory_activation(
        self, memory_id: str, activation: MemoryActivation | str
    ) -> MemoryRecord:
        return await self._memory_application.set_activation(memory_id, activation)

    async def delete_memory(self, memory_id: str) -> None:
        await self._memory_application.delete(memory_id)

    async def rebuild_memory_index(self) -> int:
        return await self._memory_application.rebuild_index()

    async def export_project_memories(self, destination: Path) -> tuple[Path, ...]:
        return await self._memory_application.export_project(destination)

    async def draft_skill_from_memory(self, memory_id: str) -> str:
        return await self._memory_application.draft_skill(memory_id)

    async def set_memory_enabled(self, enabled: bool, *, config_file: Path) -> None:
        await self._memory_application.set_enabled(
            enabled,
            config_file=config_file,
            state_root=self.state_root,
            workspace=self.workspace,
        )

    def session_exists(self, session_id: str) -> bool:
        return self._sessions.exists(session_id)

    def load_session_records(self, session_id: str) -> tuple[EventRecord, ...]:
        return self._sessions.load_records(session_id)

    def load_session_messages(self, session_id: str) -> tuple[Message, ...]:
        return self._sessions.load_messages(session_id)

    def start_run(self, request: RunRequest) -> RunHandle:
        return self._runs.start(request)

    def list_sessions(self) -> tuple[SessionMetadata, ...]:
        return self._sessions.list()

    def rewind_session(
        self,
        session_id: str,
        record_id: str,
        *,
        include_selected: bool = False,
    ) -> EventRecord:
        return self._sessions.rewind(
            session_id,
            record_id,
            include_selected=include_selected,
        )

    async def aclose(self) -> None:
        await self._lifecycle.close()


__all__ = ["RunHandle", "Windcode"]
