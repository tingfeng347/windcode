from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from windcode.application.configuration import ConfigurationApplication
from windcode.auth import CredentialStore
from windcode.domain.errors import RequiredExtensionError, RequiredExtensionStartupError
from windcode.extensions.commands import CommandRoute
from windcode.extensions.mcp import McpServerState
from windcode.extensions.mcp.catalog import McpToolDefinition
from windcode.extensions.models import (
    CapabilityRecord,
    ExtensionSnapshot,
    ManagementResult,
)
from windcode.extensions.plugins.installer import InstallResult
from windcode.extensions.runtime import RunExtensions
from windcode.extensions.service import ExtensionService
from windcode.extensions.skills.loader import SkillLoader
from windcode.extensions.skills.tools import SkillCatalog, SkillSearchResult
from windcode.extensions.state import ExtensionStateStore, ManagementAuditRecord
from windcode.runtime.parent_run import RunExtensionState
from windcode.runtime.run_handle import RunHandle
from windcode.tools import ToolRegistry


@dataclass(frozen=True, slots=True)
class McpStartupStatus:
    total: int = 0
    loaded: int = 0
    failed_servers: tuple[str, ...] = ()
    lazy: int = 0


class _ExtensionGeneration:
    def __init__(
        self,
        extensions: RunExtensions,
        startup_task: asyncio.Task[None],
        run_state: RunExtensionState,
    ) -> None:
        self.extensions = extensions
        self.startup_task = startup_task
        self.run_state = run_state
        self.leases = 0
        self.idle = asyncio.Event()
        self.idle.set()

    def acquire(self) -> None:
        self.leases += 1
        self.idle.clear()

    def release(self) -> None:
        if self.leases == 0:
            return
        self.leases -= 1
        if self.leases == 0:
            self.idle.set()


class ExtensionRunLease:
    """Pin one extension generation until its run has fully completed."""

    def __init__(self, generation: _ExtensionGeneration) -> None:
        self._generation = generation
        self._released = False
        generation.acquire()

    @property
    def state(self) -> RunExtensionState:
        return self._generation.run_state

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._generation.release()

    def bind(self, start: Callable[[RunExtensionState], RunHandle]) -> RunHandle:
        try:
            handle = start(self.state)
        except BaseException:
            self.release()
            raise
        handle.add_done_callback(lambda _handle: self.release())
        return handle


class ExtensionApplication:
    """Own extension discovery and generation-scoped MCP runtime state."""

    def __init__(
        self,
        configuration: ConfigurationApplication,
        credential_store: CredentialStore,
        *,
        workspace: Path,
        state_root: Path,
        user_skill_root: Path,
    ) -> None:
        self.configuration = configuration
        self.credential_store = credential_store
        self.workspace = workspace
        self.state_root = state_root
        self.user_skill_root = user_skill_root
        self.service: ExtensionService | None = None
        self._current: _ExtensionGeneration | None = None
        self._retirements: set[asyncio.Task[None]] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._opened = False

    def _require_service(self) -> ExtensionService:
        if not self._opened or self.service is None:
            raise RuntimeError("manage extensions inside the Windcode async context")
        return self.service

    def _require_generation(self) -> _ExtensionGeneration:
        if self._current is None:
            raise RuntimeError("extension runtime is not initialized")
        return self._current

    async def open(self) -> None:
        async with self._lifecycle_lock:
            config = self.configuration.current
            extension_root = self.state_root / "extensions"
            self.service = ExtensionService(
                config.extensions,
                self.workspace,
                ExtensionStateStore(extension_root / "state.json"),
                extension_root / "plugins",
                user_skill_root=self.user_skill_root,
            )
            await self.service.reload()
            self._current = self._create_generation()
            self._opened = True

    def _create_generation(self) -> _ExtensionGeneration:
        service = self.service
        if service is None:
            raise RuntimeError("extension service is not initialized")
        config = self.configuration.current
        catalogs: dict[str, tuple[McpToolDefinition, ...]] = {}
        selected_tools: set[str] = set()
        extensions = RunExtensions.create(
            service.snapshot,
            session_id="client",
            run_id="startup",
            credential_store=self.credential_store,
            max_content_bytes=config.extensions.max_content_bytes,
            connect_timeout=config.extensions.connect_timeout_seconds,
            call_timeout=config.extensions.call_timeout_seconds,
            network_enabled=config.sandbox.network_enabled,
            mcp_tool_catalogs=catalogs,
        )
        startup_task = asyncio.create_task(self._start_required_mcp(extensions))
        state = RunExtensionState(
            snapshot=service.snapshot,
            credential_store=self.credential_store,
            mcp_runtime=extensions.mcp,
            startup_task=startup_task,
            tool_catalogs=catalogs,
            selected_tools=selected_tools,
            direct_tool_limit=config.extensions.direct_tool_limit,
        )
        return _ExtensionGeneration(extensions, startup_task, state)

    async def _start_required_mcp(self, extensions: RunExtensions) -> None:
        try:
            ready_required_servers = await extensions.mcp.activate_required()
            await extensions.mcp_capabilities.register_direct_tools(
                ToolRegistry(),
                direct_tool_limit=self.configuration.current.extensions.direct_tool_limit,
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

    async def wait_required(self) -> None:
        if self._current is not None:
            await asyncio.shield(self._current.startup_task)

    @property
    def required_loading(self) -> bool:
        current = self._current
        return current is not None and not current.startup_task.done()

    @property
    def startup_status(self) -> McpStartupStatus:
        if self._current is None:
            return McpStartupStatus()
        runtime = self._current.extensions.mcp
        loaded = len(runtime.ready_server_ids)
        failed = runtime.failed_server_ids
        lazy = sum(
            runtime.state(server_id) is McpServerState.DISCOVERED
            for server_id in runtime.server_ids
        )
        return McpStartupStatus(len(runtime.server_ids), loaded, failed, lazy)

    @property
    def snapshot(self) -> ExtensionSnapshot:
        return self._require_service().snapshot

    async def list_capabilities(self) -> tuple[CapabilityRecord, ...]:
        return await self._require_service().list_capabilities()

    async def inspect(self, identifier: str) -> tuple[CapabilityRecord, ...]:
        return await self._require_service().inspect(identifier)

    async def install_local(self, path: Path, *, enable: bool = False) -> InstallResult:
        return await self._require_service().install_local(path, enable=enable)

    async def set_enabled(self, identifier: str, enabled: bool) -> ManagementResult:
        return await self._require_service().set_enabled(identifier, enabled)

    async def trust_workspace(self, workspace: Path, trusted: bool) -> ManagementResult:
        return await self._require_service().trust_workspace(workspace, trusted)

    def command_routes(self, *, reserved: frozenset[str]) -> tuple[CommandRoute, ...]:
        return self._require_service().command_routes(reserved=reserved)

    def search_skills(self, query: str) -> tuple[SkillSearchResult, ...]:
        config = self.configuration.current
        catalog = SkillCatalog(
            self.snapshot,
            SkillLoader(max_content_bytes=config.extensions.max_content_bytes),
        )
        return catalog.search(query)

    @property
    def audit_records(self) -> tuple[ManagementAuditRecord, ...]:
        return self._require_service().audit_records

    async def reload(self) -> ManagementResult:
        async with self._lifecycle_lock:
            result = await self._require_service().reload()
            previous = self._current
            self._current = self._create_generation()
            if previous is not None:
                retirement = asyncio.create_task(self._retire(previous))
                self._retirements.add(retirement)
                retirement.add_done_callback(self._retirement_done)
            return result

    def _retirement_done(self, task: asyncio.Task[None]) -> None:
        self._retirements.discard(task)
        if not task.cancelled():
            task.exception()

    def acquire_run(self) -> ExtensionRunLease:
        return ExtensionRunLease(self._require_generation())

    def bind_run(self, start: Callable[[RunExtensionState], RunHandle]) -> RunHandle:
        return self.acquire_run().bind(start)

    async def _retire(self, generation: _ExtensionGeneration) -> None:
        await generation.idle.wait()
        await self._close_generation(generation)

    @staticmethod
    async def _stop_startup(generation: _ExtensionGeneration) -> None:
        startup = generation.startup_task
        if not startup.done():
            startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)

    @classmethod
    async def _close_generation(cls, generation: _ExtensionGeneration) -> None:
        await cls._stop_startup(generation)
        generation.extensions.mcp.observer = None
        await generation.extensions.aclose()

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            current = self._current
            if current is None:
                self._opened = False
                return
            await self._stop_startup(current)
            await current.idle.wait()
            if self._retirements:
                await asyncio.gather(*tuple(self._retirements), return_exceptions=True)
                self._retirements.clear()
            try:
                await self._close_generation(current)
            finally:
                self._current = None
                self._opened = False
