from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from uuid import uuid4

from windcode.config import AppConfig
from windcode.domain.events import RunRequest
from windcode.domain.messages import Message, heal_dangling_tool_calls, message_from_dict
from windcode.domain.tools import ToolContext
from windcode.extensions import CapabilityKind
from windcode.extensions.runtime import RunExtensions
from windcode.observability import DynamicRedactor
from windcode.policy import PolicyEngine, PolicyRequest
from windcode.policy.rules import CommandRuleStore
from windcode.providers import ModelTarget
from windcode.runtime import (
    AgentLoop,
    ContextWindow,
    ModelSession,
    RunBudgets,
    RunControl,
    RunIdentity,
    RunJournal,
    RunObservers,
    ScheduledCall,
    ToolRuntime,
    ToolScheduler,
)
from windcode.runtime.event_bus import EventBus
from windcode.runtime.parent_access import ParentAccess, ParentAccessBuilder
from windcode.runtime.parent_run import (
    ParentRun,
    ParentRunPreparation,
    RunCompletion,
    RunExtensionState,
)
from windcode.runtime.prompts import build_system_prompt
from windcode.runtime.resources import RunResources
from windcode.runtime.run_handle import RunHandle
from windcode.runtime.run_memory import RunMemory
from windcode.runtime.subagents import (
    ChildRunPreparer,
    ChildRunProfile,
    ChildRuntime,
    SubagentCompletionSource,
    SubagentCoordinator,
    VerificationRunner,
)
from windcode.runtime.subagents.child_execution import (
    AggregateRunControl,
    ChildAgentLoop,
    ChildToolScheduler,
)
from windcode.runtime.subagents.child_support import (
    ChildRunSupport,
    build_child_prompt,
    force_plan_on_permission_update,
)
from windcode.sandbox import SandboxPreset
from windcode.sessions import ArtifactStore, SessionStore, ancestor_chain
from windcode.tools import ToolRegistry, add_subagent_tools
from windcode.worktrees import WorktreeManager


class RunBuilder:
    def __init__(
        self,
        config: AppConfig,
        *,
        state_root: Path,
        user_storage_root: Path,
        base_tools: ToolRegistry,
        model_chain: Callable[[str | None], tuple[ModelTarget, ...]],
        extensions: RunExtensionState,
    ) -> None:
        self.config = config
        self.state_root = state_root
        self.user_storage_root = user_storage_root
        self.base_tools = base_tools
        self.model_chain = model_chain
        self.extensions = extensions
        self.access_builder = ParentAccessBuilder(
            config,
            state_root=state_root,
            base_tools=base_tools,
        )
        self._coordinators: dict[
            str, tuple[SubagentCoordinator, SubagentCompletionSource, EventBus]
        ] = {}

    def _evict_idle_coordinators(self) -> None:
        """Drop reused coordinators whose background subagents have all finished."""
        for session_id in [
            sid
            for sid, (coordinator, _, _) in self._coordinators.items()
            if not coordinator.has_running()
        ]:
            del self._coordinators[session_id]

    def start(self, request: RunRequest) -> RunHandle:
        self._evict_idle_coordinators()
        preparation = self.prepare_parent(request)
        resources = self.resources(preparation)
        redactor = DynamicRedactor()
        resources.trace_store.bind_redactor(redactor)
        extensions = self._run_extensions(preparation, resources, redactor)
        access = self.access_builder.prepare(
            preparation.workspace,
            preparation.session,
            request,
            extensions,
            self.extensions.snapshot,
            self.extensions.tool_catalogs,
            self.extensions.selected_tools,
        )
        identity = RunIdentity(preparation.session.metadata.session_id, preparation.run_id)
        model_chain = self.model_chain(request.model)
        memory = RunMemory(
            self.config.memory,
            state_root=self.state_root,
            workspace=preparation.workspace,
            request=request,
            identity=identity,
            registry=access.registry,
            event_bus=resources.event_bus,
            model=model_chain[0],
        )
        control = self._control(request)
        session_id = preparation.session.metadata.session_id
        reused = self._coordinators.get(session_id)
        if reused is not None and reused[0].has_running():
            coordinator, completion_source, _ = reused
            coordinator.reattach(
                event_bus=resources.event_bus,
                event_observer=extensions.subagent_lifecycle,
                parent_run_id=preparation.run_id,
                permission_mode=access.permission_mode,
                prepare_child=self.bind_child(access.child_tools, default_model=request.model),
            )
        else:
            completion_source = SubagentCompletionSource()
            coordinator = self._coordinator(
                preparation, request, resources, access, extensions, completion_source
            )
        self._coordinators[session_id] = (coordinator, completion_source, resources.event_bus)
        add_subagent_tools(access.registry, coordinator)
        system_prompt = self._system_prompt(preparation, access, extensions, memory)
        scheduler = self._scheduler(preparation, access, extensions, control)
        completion = RunCompletion(extensions, memory)
        loop = AgentLoop(
            identity=identity,
            model=ModelSession(
                model_chain,
                system_prompt((), extensions.mcp.server_ids),
                stream_idle_timeout_seconds=(self.config.budgets.model_stream_idle_timeout_seconds),
            ),
            tools=ToolRuntime(scheduler, control),
            journal=RunJournal(resources.event_bus, close_on_exit=False),
            context=ContextWindow(
                token_estimator=resources.token_estimator,
                artifact_store=resources.artifact_store,
                preserve_recent_turns=self.config.context.preserve_recent_turns,
                max_tool_result_chars=self.config.context.max_tool_result_chars,
            ),
            observers=RunObservers(
                sourced_context=extensions.drain_context,
                compact=extensions.compact_lifecycle,
                completion=completion,
            ),
            inbound_message_source=completion_source,
        )
        parent = ParentRun(
            request,
            preparation,
            self.extensions,
            extensions,
            redactor,
            memory,
            access,
            resources,
            coordinator,
            loop,
            completion,
            system_prompt,
        )
        task = asyncio.create_task(parent.run())
        return RunHandle(
            task,
            resources.event_bus,
            control,
            after_sequence=preparation.session.metadata.next_sequence - 1,
            coordinator=coordinator,
            policy=access.policy,
            loop=loop,
        )

    def _run_extensions(
        self,
        preparation: ParentRunPreparation,
        resources: RunResources,
        redactor: DynamicRedactor,
    ) -> RunExtensions:
        extensions = RunExtensions.create(
            self.extensions.snapshot,
            session_id=preparation.session.metadata.session_id,
            run_id=preparation.run_id,
            credential_store=self.extensions.credential_store,
            max_content_bytes=self.config.extensions.max_content_bytes,
            connect_timeout=self.config.extensions.connect_timeout_seconds,
            call_timeout=self.config.extensions.call_timeout_seconds,
            observe_secret=redactor.register,
            artifact_store=preparation.artifact_store,
            network_enabled=self.config.sandbox.network_enabled,
            mcp_runtime=self.extensions.mcp_runtime,
            mcp_tool_catalogs=self.extensions.tool_catalogs,
        )
        extensions.event_observer = lambda event: resources.event_bus.publish(event, durable=True)
        return extensions

    def _control(self, request: RunRequest) -> RunControl:
        control = RunControl(
            RunBudgets(
                max_model_steps=self.config.budgets.max_model_steps,
                max_tool_calls=self.config.budgets.max_tool_calls,
                max_runtime_seconds=self.config.budgets.max_runtime_seconds,
            )
        )
        if request.compact_before_run:
            control.request_compaction()
        return control

    def _coordinator(
        self,
        preparation: ParentRunPreparation,
        request: RunRequest,
        resources: RunResources,
        access: ParentAccess,
        extensions: RunExtensions,
        completion_source: SubagentCompletionSource | None = None,
    ) -> SubagentCoordinator:
        prepare_child = self.bind_child(
            access.child_tools,
            default_model=request.model,
        )
        return SubagentCoordinator(
            parent_session_id=preparation.session.metadata.session_id,
            parent_run_id=preparation.run_id,
            workspace=preparation.workspace,
            permission_mode=access.permission_mode,
            config=self.config.subagents,
            event_bus=resources.event_bus,
            prepare_child=prepare_child,
            worktrees=WorktreeManager(
                worktrees_root=self.state_root / "worktrees",
                fallback_worktrees_root=self.user_storage_root / "worktrees",
            ),
            verification=VerificationRunner(
                sandbox=access.sandbox,
                sandbox_policy=access.sandbox_policy,
                timeout_seconds=self.config.budgets.shell_timeout_seconds,
            ),
            network_enabled=self.config.sandbox.network_enabled,
            event_observer=extensions.subagent_lifecycle,
            completion_source=completion_source,
        )

    def _system_prompt(
        self,
        preparation: ParentRunPreparation,
        access: ParentAccess,
        extensions: RunExtensions,
        memory: RunMemory,
    ) -> Callable[[tuple[str, ...], tuple[str, ...]], str]:
        unavailable_servers = tuple(
            (
                record.public_name,
                "未信任当前工作区, 需要执行 extensions trust 后 reload",
            )
            for record in self.extensions.snapshot.capabilities
            if record.kind is CapabilityKind.MCP_SERVER and record.enabled and not record.trusted
        )

        def make_prompt(direct_servers: tuple[str, ...], search_servers: tuple[str, ...]) -> str:
            startup_unavailable = tuple(
                (server_id, "启动连接失败, 本轮已降级且不会阻断普通消息")
                for server_id in extensions.mcp.failed_server_ids
            )
            prompt = build_system_prompt(
                workspace=preparation.workspace,
                permission_mode=access.policy.mode,
                instructions=access.instructions,
                tools=access.registry,
                delegation_mode=self.config.subagents.mode,
                skills=extensions.skills.search(),
                mcp_direct_servers=direct_servers,
                mcp_search_servers=search_servers,
                mcp_unavailable_servers=(*unavailable_servers, *startup_unavailable),
                memory_enabled=memory.enabled,
            )
            if memory.context:
                prompt += f"\n\n{memory.context}"
            return prompt

        return make_prompt

    def _scheduler(
        self,
        preparation: ParentRunPreparation,
        access: ParentAccess,
        extensions: RunExtensions,
        control: RunControl,
    ) -> ToolScheduler:
        def record_session_approval(request: PolicyRequest) -> None:
            payload: dict[str, object] = {
                "workspace": str(preparation.workspace),
                "tool_name": request.tool_name,
            }
            if request.proposed_rule is not None:
                payload["rule"] = request.proposed_rule.model_copy(
                    update={"source": "session"}
                ).model_dump(mode="json")
            else:
                payload["effects"] = sorted(effect.value for effect in request.effects)
            preparation.session.append("session_approval", payload, durable=True)

        scheduler = ToolScheduler(
            access.registry,
            access.policy,
            before_policy=extensions.before_policy,
            permission_observer=extensions.permission_requested,
            after_execute=extensions.after_execute,
            session_approval_recorder=record_session_approval,
        )

        async def run_hook_command(command: str, origin: str, hook_context: object) -> str:
            del hook_context
            scheduled = ScheduledCall(uuid4().hex, "shell", {"command": command}, origin=origin)
            results = await scheduler.execute(
                (scheduled,),
                ToolContext(
                    preparation.workspace,
                    preparation.run_id,
                    lambda: control.cancelled,
                ),
            )
            result = results[0].result
            if result.is_error:
                raise RuntimeError(result.output)
            return result.output

        extensions.hooks.executor.command_runner = run_hook_command
        return scheduler

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

    def bind_child(
        self,
        parent_tools: ToolRegistry,
        *,
        default_model: str | None,
    ) -> ChildRunPreparer:
        support = ChildRunSupport(
            config=self.config,
            state_root=self.state_root,
            parent_tools=parent_tools,
            extension_snapshot=self.extensions.snapshot,
        )
        return partial(self.prepare_child, support=support, default_model=default_model)

    def prepare_child(
        self,
        profile: ChildRunProfile,
        *,
        support: ChildRunSupport,
        default_model: str | None,
    ) -> ChildRuntime:
        record = profile.record
        spec = record.spec
        plan = support.prepare_tools(
            record,
            profile.workspace,
            profile.parent_permission,
            profile.collaboration,
        )
        child_session_id = record.child_session_id or uuid4().hex
        child_record = replace(record, child_session_id=child_session_id)
        session = SessionStore.create(self.state_root / "sessions", child_session_id)
        child_run_id = uuid4().hex
        resources = RunResources.create(
            session=session,
            run_id=child_run_id,
            state_root=self.state_root,
            artifact_store=ArtifactStore(session.session_dir),
            trace_config=self.config.trace,
            context_config=self.config.context,
        )
        skill_runtime = support.register_skills(
            plan,
            resources.event_bus,
            session_id=child_session_id,
            run_id=child_run_id,
        )
        scheduler = ChildToolScheduler(
            plan.registry,
            PolicyEngine(
                plan.effective_permission,
                sandbox_enabled=plan.effective_preset is not SandboxPreset.DANGER_FULL_ACCESS,
                sandbox_available=plan.sandbox_available,
                rule_store=CommandRuleStore(self.state_root, profile.workspace),
            ),
        )
        control = AggregateRunControl(
            RunBudgets(
                max_model_steps=self.config.subagents.max_model_steps,
                max_tool_calls=self.config.subagents.max_tool_calls,
                max_runtime_seconds=self.config.subagents.max_runtime_seconds,
            ),
            profile.aggregate_budget,
        )
        system_prompt = support.system_prompt(
            record,
            profile.workspace,
            plan,
            skill_runtime,
        )
        loop = ChildAgentLoop(
            record=child_record,
            approval_router=profile.approval_router,
            identity=RunIdentity(child_session_id, child_run_id),
            model=ModelSession(
                self.model_chain(spec.model or default_model),
                system_prompt,
                stream_idle_timeout_seconds=self.config.budgets.model_stream_idle_timeout_seconds,
            ),
            tools=ToolRuntime(scheduler, control),
            journal=RunJournal(resources.event_bus),
            context=ContextWindow(
                token_estimator=resources.token_estimator,
                artifact_store=resources.artifact_store,
                preserve_recent_turns=self.config.context.preserve_recent_turns,
                max_tool_result_chars=self.config.context.max_tool_result_chars,
            ),
            observers=RunObservers(sourced_context=skill_runtime.drain_context),
            inbound_message_source=(profile.collaboration if spec.peer_collaboration else None),
        )
        return ChildRuntime(
            child_record,
            control,
            resources.event_bus,
            loop,
            profile.workspace,
            build_child_prompt(child_record),
            force_plan_on_permission_update=force_plan_on_permission_update(
                spec.kind,
                plan.configured_preset,
                sandbox_available=scheduler.policy.sandbox_available,
            ),
        )
