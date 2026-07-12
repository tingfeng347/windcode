from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from windcode.auth import CredentialStore
from windcode.config.models import (
    EnvironmentReference,
    McpHttpConfig,
    McpStdioConfig,
    SecretReference,
)
from windcode.domain.events import (
    ExtensionEvent,
    SubagentCancelled,
    SubagentCompleted,
    SubagentEvent,
    SubagentFailed,
    SubagentStarted,
)
from windcode.domain.messages import SourcedContextMessage
from windcode.domain.tools import ToolContext, ToolResult
from windcode.extensions.events import extension_event
from windcode.extensions.hooks.dispatcher import HookDispatcher
from windcode.extensions.hooks.executor import HookExecutor
from windcode.extensions.hooks.models import HookContext, HookDefinition, HookEvent
from windcode.extensions.mcp.catalog import McpToolDefinition
from windcode.extensions.mcp.client import McpClient, ResolvedHttpServer, ResolvedStdioServer
from windcode.extensions.mcp.runtime import McpRuntime
from windcode.extensions.mcp.tools import McpCapabilityService
from windcode.extensions.models import CapabilityKind, ExtensionSnapshot
from windcode.extensions.skills.loader import SkillLoader
from windcode.extensions.skills.tools import SkillCatalog
from windcode.policy.models import PolicyDecision, PolicyRequest
from windcode.runtime.scheduler import PolicyConstraints, ScheduledCall
from windcode.sessions.artifacts import ArtifactStore

SecretObserver = Callable[[str], None]
ExtensionEventObserver = Callable[[ExtensionEvent], Awaitable[object]]


def _resolve_reference(
    reference: SecretReference,
    credential_store: CredentialStore,
    observe_secret: SecretObserver | None,
) -> str:
    if isinstance(reference, EnvironmentReference):
        value = os.environ.get(reference.env)
        label = f"environment variable {reference.env}"
    else:
        value = credential_store.get(reference.credential)
        label = f"credential {reference.credential}"
    if value is None:
        raise ValueError(f"missing MCP secret reference: {label}")
    if observe_secret is not None:
        observe_secret(value)
    return value


@dataclass(slots=True)
class RunExtensions:
    snapshot: ExtensionSnapshot
    session_id: str
    run_id: str
    skills: SkillCatalog
    mcp: McpRuntime
    mcp_capabilities: McpCapabilityService
    hooks: HookDispatcher
    event_observer: ExtensionEventObserver | None = None
    owns_mcp: bool = True

    @classmethod
    def create(
        cls,
        snapshot: ExtensionSnapshot,
        *,
        session_id: str,
        run_id: str,
        credential_store: CredentialStore,
        max_content_bytes: int,
        connect_timeout: float,
        call_timeout: float,
        observe_secret: SecretObserver | None = None,
        artifact_store: ArtifactStore | None = None,
        network_enabled: bool = False,
        mcp_runtime: McpRuntime | None = None,
        mcp_tool_catalogs: dict[str, tuple[McpToolDefinition, ...]] | None = None,
    ) -> RunExtensions:
        servers: dict[str, tuple[Callable[[], McpClient], bool]] = {}
        hooks: list[HookDefinition] = []
        records = {record.capability_id: record for record in snapshot.capabilities}
        for stable_id, definition in snapshot.definitions.items():
            record = records.get(stable_id)
            if record is None or not record.enabled or not record.trusted:
                continue
            if record.kind is CapabilityKind.HOOK and isinstance(definition, HookDefinition):
                hooks.append(definition)
            elif record.kind is CapabilityKind.MCP_SERVER and isinstance(
                definition, (McpStdioConfig, McpHttpConfig)
            ):
                if isinstance(definition, McpStdioConfig):

                    def stdio_factory(
                        server: McpStdioConfig = definition,
                        source_path: Path | None = record.source.path,
                    ) -> McpClient:
                        environment = {
                            key: _resolve_reference(value, credential_store, observe_secret)
                            for key, value in server.env.items()
                        }
                        cwd = (
                            None
                            if server.cwd is None or source_path is None
                            else source_path / server.cwd
                        )
                        return McpClient(
                            ResolvedStdioServer(server.command, server.args, cwd, environment),
                            connect_timeout=connect_timeout,
                            call_timeout=call_timeout,
                        )

                    client_factory = stdio_factory
                else:

                    def http_factory(server: McpHttpConfig = definition) -> McpClient:
                        if not network_enabled:
                            raise PermissionError("MCP HTTP is disabled by the run network policy")
                        headers = {
                            key: _resolve_reference(value, credential_store, observe_secret)
                            for key, value in server.headers.items()
                        }
                        return McpClient(
                            ResolvedHttpServer(server.url, headers),
                            connect_timeout=connect_timeout,
                            call_timeout=call_timeout,
                        )

                    client_factory = http_factory

                servers[record.public_name] = (client_factory, definition.required)
        runtime = mcp_runtime or McpRuntime(servers)
        run_extensions = cls(
            snapshot,
            session_id,
            run_id,
            SkillCatalog(snapshot, SkillLoader(max_content_bytes=max_content_bytes)),
            runtime,
            McpCapabilityService(
                runtime,
                artifact_store=artifact_store,
                content_limit=max_content_bytes,
                tool_catalogs=mcp_tool_catalogs,
            ),
            HookDispatcher(tuple(hooks), HookExecutor()),
            owns_mcp=mcp_runtime is None,
        )
        run_extensions.hooks.observer = run_extensions.observe_hook
        run_extensions.mcp.observer = run_extensions.observe_mcp
        return run_extensions

    def _context(
        self,
        event: HookEvent,
        correlation_id: str,
        *,
        tool_id: str | None = None,
        status: str | None = None,
        source_id: str = "windcode",
    ) -> HookContext:
        return HookContext(
            1,
            event,
            self.session_id,
            self.run_id,
            correlation_id,
            source_id=source_id,
            tool_id=tool_id,
            status=status,
        )

    async def _emit(
        self,
        action: str,
        *,
        extension_id: str,
        source_id: str,
        status: str = "",
        hook_id: str | None = None,
        server_id: str | None = None,
        call_id: str | None = None,
    ) -> None:
        if self.event_observer is None:
            return
        from uuid import uuid4

        await self.event_observer(
            extension_event(
                event_id=uuid4().hex,
                session_id=self.session_id,
                run_id=self.run_id,
                turn=0,
                action=action,
                snapshot_generation=self.snapshot.generation,
                extension_id=extension_id,
                source_id=source_id,
                status=status,
                hook_id=hook_id,
                server_id=server_id,
                call_id=call_id,
            )
        )

    async def observe_hook(
        self,
        phase: str,
        hook: HookDefinition,
        context: HookContext,
        outcome: object,
    ) -> None:
        del outcome
        action = {
            "started": "hook_started",
            "finished": "hook_finished",
            "rejected": "hook_rejected",
        }[phase]
        await self._emit(
            action,
            extension_id=hook.source_id,
            source_id=hook.source_id,
            status=phase,
            hook_id=hook.hook_id,
            call_id=context.correlation_id,
        )

    async def observe_mcp(self, action: str, server_id: str, status: str) -> None:
        await self._emit(
            action,
            extension_id=f"mcp_server:{server_id}",
            source_id=f"mcp:{server_id}",
            status=status,
            server_id=server_id,
        )

    async def before_policy(self, call: ScheduledCall, context: ToolContext) -> PolicyConstraints:
        del context
        outcome = await self.hooks.dispatch(
            self._context(
                HookEvent.TOOL_BEFORE_POLICY,
                call.call_id,
                tool_id=call.tool_name,
                source_id=call.origin or "windcode",
            )
        )
        return PolicyConstraints(outcome.additional_effects, outcome.rejected)

    async def permission_requested(
        self, call: ScheduledCall, request: PolicyRequest, decision: PolicyDecision
    ) -> None:
        del request
        await self.hooks.dispatch(
            self._context(
                HookEvent.PERMISSION_REQUEST,
                call.call_id,
                tool_id=call.tool_name,
                status=decision.action.value,
                source_id=call.origin or "windcode",
            )
        )

    async def after_execute(
        self, call: ScheduledCall, request: PolicyRequest, result: ToolResult
    ) -> None:
        del request
        await self.hooks.dispatch(
            self._context(
                HookEvent.TOOL_AFTER,
                call.call_id,
                tool_id=call.tool_name,
                status="error" if result.is_error else "success",
                source_id=call.origin or "windcode",
            ),
            background=True,
        )

    async def lifecycle(self, event: HookEvent, *, status: str | None = None) -> None:
        await self.hooks.dispatch(
            self._context(event, self.run_id, status=status),
            background=event in {HookEvent.RUN_END, HookEvent.SESSION_END},
        )

    async def compact_lifecycle(self, phase: str) -> None:
        event = HookEvent.COMPACT_BEFORE if phase == "before" else HookEvent.COMPACT_AFTER
        await self.lifecycle(event, status=phase)

    async def subagent_lifecycle(self, event: SubagentEvent) -> None:
        if isinstance(event, SubagentStarted):
            hook_event = HookEvent.SUBAGENT_START
        elif isinstance(event, (SubagentCompleted, SubagentFailed, SubagentCancelled)):
            hook_event = HookEvent.SUBAGENT_END
        else:
            return
        await self.hooks.dispatch(
            HookContext(
                1,
                hook_event,
                self.session_id,
                self.run_id,
                event.subagent_id,
                status=event.kind,
                fields=(
                    ("subagent_id", event.subagent_id),
                    ("role", event.role),
                    ("task_name", event.task_name),
                ),
            )
        )

    async def activate_skill(self, selector: str) -> None:
        content, message = self.skills.load(selector)
        self.hooks.executor.context_messages.append(message)
        await self._emit(
            "skill_loaded",
            extension_id=content.name,
            source_id=content.source_id,
            status="loaded",
        )

    async def activate_prompt(self, selector: str) -> None:
        await self.mcp_capabilities.activate_prompt(selector)

    def activate_capability(self, selector: str, *, source_id: str = "plugin-command") -> None:
        matches = [
            record
            for record in self.snapshot.capabilities
            if record.enabled
            and record.trusted
            and (record.capability_id == selector or record.public_name == selector)
        ]
        if not matches:
            raise KeyError(f"unknown extension capability: {selector}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous extension capability: {selector}")
        record = matches[0]
        self.hooks.executor.context_messages.append(
            SourcedContextMessage(
                source_id,
                f"The user selected extension capability {record.capability_id}. "
                "Use it through the normal tool and permission workflow.",
            )
        )

    def drain_context(self) -> tuple[SourcedContextMessage, ...]:
        return (
            *self.hooks.executor.drain_context(),
            *self.mcp_capabilities.drain_context(),
        )

    async def aclose(self) -> None:
        await self.hooks.aclose()
        if self.owns_mcp:
            await self.mcp.aclose()
