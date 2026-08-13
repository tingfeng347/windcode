from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from windcode.auth import CredentialStore
from windcode.domain.errors import RequiredExtensionError
from windcode.domain.events import RunCancelled, RunRequest, RunResult
from windcode.domain.messages import Message
from windcode.extensions import ExtensionSnapshot
from windcode.extensions.hooks import HookEvent
from windcode.extensions.mcp import McpRuntime, McpServerState
from windcode.extensions.mcp.catalog import McpToolDefinition
from windcode.extensions.runtime import RunExtensions
from windcode.observability import DynamicRedactor
from windcode.runtime import AgentLoop
from windcode.runtime.parent_access import ParentAccess
from windcode.runtime.resources import RunResources
from windcode.runtime.run_memory import RunMemory
from windcode.runtime.subagents import SubagentCoordinator
from windcode.sessions import ArtifactStore, SessionStatus, SessionStore


@dataclass(frozen=True, slots=True)
class ParentRunPreparation:
    workspace: Path
    existing_session: bool
    session: SessionStore
    initial_messages: tuple[Message, ...]
    run_id: str
    artifact_store: ArtifactStore


@dataclass(frozen=True, slots=True)
class RunExtensionState:
    """Extension generation and shared MCP state pinned when a parent run starts."""

    snapshot: ExtensionSnapshot
    credential_store: CredentialStore
    mcp_runtime: McpRuntime | None
    startup_task: asyncio.Task[None] | None
    tool_catalogs: dict[str, tuple[McpToolDefinition, ...]]
    selected_tools: set[str]
    direct_tool_limit: int


@dataclass(slots=True)
class RunCompletion:
    extensions: RunExtensions
    memory: RunMemory
    observed: bool = False

    async def __call__(self, result: RunResult) -> None:
        self.observed = True
        await self.extensions.lifecycle(HookEvent.RUN_END, status=result.status)
        await self.memory.complete(result)


@dataclass(frozen=True, slots=True)
class ParentRun:
    request: RunRequest
    preparation: ParentRunPreparation
    extension_state: RunExtensionState
    extensions: RunExtensions
    redactor: DynamicRedactor
    memory: RunMemory
    access: ParentAccess
    resources: RunResources
    coordinator: SubagentCoordinator
    loop: AgentLoop
    completion: RunCompletion
    system_prompt: Callable[[tuple[str, ...], tuple[str, ...]], str]

    async def run(self) -> RunResult:
        observer_token = self.extensions.mcp.bind_observer(self.extensions.observe_mcp)
        try:
            await self.memory.prepare()
            await self._prepare_mcp_tools()
            await self.memory.publish_recalled()
            await self._start_lifecycle()
            if self.preparation.existing_session:
                await self.coordinator.recover()
            result = await self.loop.run(
                self.request.prompt,
                self.preparation.workspace,
                self.preparation.initial_messages,
            )
            if not self.completion.observed:
                try:
                    await self.extensions.lifecycle(HookEvent.RUN_END, status=result.status)
                except RequiredExtensionError:
                    # A primary failed/cancelled terminal record already owns the outcome.
                    pass
            return result
        except RequiredExtensionError as exc:
            await self.loop.record_startup_failure(exc)
            await self._observe_error()
            raise
        except asyncio.CancelledError:
            await self._observe_cancelled()
            return RunResult(status="cancelled")
        except BaseException:
            await self._observe_error()
            raise
        finally:
            await asyncio.gather(
                self.coordinator.shutdown("parent run ended"), return_exceptions=True
            )
            await asyncio.gather(
                self.extensions.lifecycle(HookEvent.SESSION_END), return_exceptions=True
            )
            await asyncio.gather(self.extensions.aclose(), return_exceptions=True)
            self.extensions.mcp.reset_observer(observer_token)
            self.redactor.clear()
            await self.resources.event_bus.close()

    async def _prepare_mcp_tools(self) -> None:
        if self.extension_state.startup_task is not None:
            await asyncio.shield(self.extension_state.startup_task)
        ready_required_servers = tuple(
            server_id
            for server_id in self.extensions.mcp.required_server_ids
            if self.extensions.mcp.state(server_id) is McpServerState.READY
        )
        direct_tools = await self.extensions.mcp_capabilities.register_direct_tools(
            self.access.registry,
            direct_tool_limit=self.extension_state.direct_tool_limit,
            server_ids=ready_required_servers,
        )
        await self.extensions.mcp_capabilities.register_direct_tools(
            self.access.child_tools,
            direct_tool_limit=self.extension_state.direct_tool_limit,
            server_ids=ready_required_servers,
        )
        await self.extensions.mcp_capabilities.register_selected_tools(
            self.access.registry, self.extension_state.selected_tools
        )
        await self.extensions.mcp_capabilities.register_selected_tools(
            self.access.child_tools, self.extension_state.selected_tools
        )
        direct_servers = ready_required_servers if direct_tools else ()
        search_servers = tuple(
            server_id
            for server_id in self.extensions.mcp.server_ids
            if server_id not in set(direct_servers)
        )
        self.loop.system_prompt = self.system_prompt(direct_servers, search_servers)

    async def _start_lifecycle(self) -> None:
        if not self.preparation.existing_session:
            await self.extensions.lifecycle(HookEvent.SESSION_START)
        await self.extensions.lifecycle(HookEvent.USER_SUBMIT)
        await self.extensions.lifecycle(HookEvent.RUN_START)
        prompt_parts = self.request.prompt.strip().split(maxsplit=1)
        if prompt_parts and prompt_parts[0].startswith("$"):
            await self.extensions.activate_skill(prompt_parts[0])
        elif prompt_parts and prompt_parts[0].startswith("@prompt:"):
            await self.extensions.activate_prompt(prompt_parts[0].removeprefix("@prompt:"))
        elif prompt_parts and prompt_parts[0].startswith("@capability:"):
            self.extensions.activate_capability(prompt_parts[0].removeprefix("@capability:"))

    async def _observe_error(self) -> None:
        try:
            await self.extensions.lifecycle(HookEvent.RUN_ERROR, status="error")
        except BaseException:
            pass

    async def _observe_cancelled(self) -> None:
        """Publish RunCancelled and mark the session as cancelled."""
        try:
            await self.resources.event_bus.publish(
                RunCancelled(
                    event_id=uuid4().hex,
                    session_id=self.preparation.session.metadata.session_id,
                    run_id=self.preparation.run_id,
                    turn=0,
                ),
                durable=True,
            )
            self.preparation.session.set_status(SessionStatus.CANCELLED)
        except BaseException:
            pass
