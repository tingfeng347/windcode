from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast
from uuid import uuid4

from platformdirs import user_state_path

from windcode.config import AppConfig, PermissionMode
from windcode.context import TokenEstimator
from windcode.domain.events import (
    AgentEventType,
    ApprovalResponse,
    RunRequest,
    RunResponse,
    RunResult,
)
from windcode.domain.messages import Message, Role, TextBlock, message_from_dict
from windcode.domain.subagents import SubagentRecord, SubagentResult
from windcode.domain.tools import Tool, ToolEffect
from windcode.instructions import load_instructions
from windcode.observability import TraceStore
from windcode.policy import PolicyEngine, PolicyRequest
from windcode.providers import ModelTarget, ModelTransport, TransportRegistry
from windcode.runtime.control import RunBudgets, RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.loop import AgentLoop
from windcode.runtime.prompts import build_system_prompt
from windcode.runtime.scheduler import ToolScheduler
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
from windcode.tools import ToolRegistry, add_subagent_tools, create_builtin_registry
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
    ) -> None:
        self._task = task
        self._event_bus = event_bus
        self._control = control
        self._after_sequence = after_sequence
        self._coordinator = coordinator
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
    ) -> None:
        self.config = config
        self.state_root = (state_root or user_state_path("windcode")).expanduser().resolve()
        self.transport_registry = TransportRegistry()
        self.tool_registry: ToolRegistry | None = None
        self._default_chain: list[str] = []
        self._handles: set[RunHandle] = set()
        self._entered = False

    @classmethod
    def open(
        cls,
        config: AppConfig | Mapping[str, Any] | None = None,
        *,
        state_root: Path | None = None,
    ) -> Self:
        parsed = config if isinstance(config, AppConfig) else AppConfig.model_validate(config or {})
        return cls(parsed, state_root=state_root)

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("Windcode client is already open")
        self._entered = True
        self.state_root.mkdir(parents=True, exist_ok=True)
        if self.config.providers:
            self.transport_registry = TransportRegistry.from_config(self.config)
            if self.config.primary_provider is not None:
                self._default_chain = [
                    self.config.primary_provider,
                    *self.config.fallback_chain,
                ]
        self.tool_registry = create_builtin_registry(
            shell_timeout=self.config.budgets.shell_timeout_seconds,
        )
        return self

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

    def _model_chain(self, requested: str | None) -> tuple[ModelTarget, ...]:
        if requested is not None and requested in self.transport_registry.aliases:
            return (self.transport_registry.get(requested),)
        if not self._default_chain:
            raise RuntimeError("no model transport is configured")
        chain = tuple(self.transport_registry.get(alias) for alias in self._default_chain)
        if requested is not None:
            chain = (replace(chain[0], model=requested), *chain[1:])
        return chain

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
        return tuple(
            message_from_dict(record.payload)
            for record in self.load_session_records(session_id)
            if record.record_type == "conversation_message"
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
            initial_messages = tuple(
                message_from_dict(record.payload)
                for record in records
                if record.record_type == "conversation_message"
            )
        run_id = uuid4().hex
        trace = TraceStore(
            run_id,
            root=self.state_root / "traces",
            include_tool_arguments=self.config.trace.include_tool_arguments,
        )
        bus = EventBus(session, trace)
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
        )
        add_subagent_tools(run_registry, coordinator)
        system_prompt = build_system_prompt(
            workspace=workspace,
            permission_mode=mode,
            instructions=instructions,
            tools=run_registry,
            delegation_mode=self.config.subagents.mode,
        )

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
            session_approval_recorder=record_session_approval,
        )
        loop = AgentLoop(
            session_id=session.metadata.session_id,
            run_id=run_id,
            model_chain=self._model_chain(request.model),
            scheduler=scheduler,
            control=control,
            event_bus=bus,
            system_prompt=system_prompt,
            token_estimator=TokenEstimator(
                self.config.context.window_tokens,
                compaction_threshold=self.config.context.compaction_threshold,
            ),
            artifact_store=ArtifactStore(session.session_dir),
            preserve_recent_turns=self.config.context.preserve_recent_turns,
            max_tool_result_chars=self.config.context.max_tool_result_chars,
            close_event_bus=False,
        )
        after_sequence = session.metadata.next_sequence - 1

        async def run_with_subagents() -> RunResult:
            try:
                if existing_session:
                    await coordinator.recover()
                return await loop.run(request.prompt, workspace, initial_messages)
            finally:
                await coordinator.shutdown("parent run ended")
                await bus.close()

        task = asyncio.create_task(run_with_subagents())
        handle = RunHandle(
            task,
            bus,
            control,
            after_sequence=after_sequence,
            coordinator=coordinator,
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
        await self.transport_registry.aclose()
        self._entered = False


__all__ = ["RunHandle", "Windcode"]
