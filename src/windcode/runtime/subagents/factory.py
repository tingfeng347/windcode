from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from windcode.config import AppConfig, PermissionMode
from windcode.context import TokenEstimator
from windcode.domain.subagents import SubagentRecord, SubagentTaskKind
from windcode.domain.tools import ToolContext
from windcode.extensions import ExtensionSnapshot
from windcode.extensions.events import extension_event
from windcode.extensions.skills.loader import SkillLoader
from windcode.extensions.skills.tools import (
    SkillActivationResult,
    SkillCatalog,
    SkillRuntime,
    register_skill_tools,
)
from windcode.instructions import load_instructions
from windcode.observability import TraceStore
from windcode.policy import ApprovalChoice, PolicyDecision, PolicyEngine, PolicyRequest
from windcode.providers import ModelTarget
from windcode.runtime.control import BudgetExceeded, RunBudgets, RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.loop import AgentBlocked, AgentLoop
from windcode.runtime.prompts import build_system_prompt
from windcode.runtime.scheduler import ScheduledCall, ScheduledResult, ToolScheduler
from windcode.runtime.subagents.approvals import ApprovalRouter
from windcode.runtime.subagents.budgets import AggregateBudget, AggregateBudgetExceeded
from windcode.runtime.subagents.roles import ROLE_POLICIES, resolve_role_tools
from windcode.sandbox import BubblewrapSandbox, detect_bubblewrap
from windcode.sessions import ArtifactStore, SessionStore
from windcode.tools import ToolRegistry
from windcode.tools.shell import ShellTool


def _git_common_directory(workspace: Path) -> Path | None:
    marker = workspace / ".git"
    if not marker.is_file():
        return marker.resolve() if marker.is_dir() else None
    content = marker.read_text(encoding="utf-8").strip()
    if not content.startswith("gitdir: "):
        return None
    git_directory = Path(content.removeprefix("gitdir: "))
    if not git_directory.is_absolute():
        git_directory = workspace / git_directory
    git_directory = git_directory.resolve()
    common_marker = git_directory / "commondir"
    if not common_marker.is_file():
        return git_directory
    common = Path(common_marker.read_text(encoding="utf-8").strip())
    return (git_directory / common).resolve()


class AggregateRunControl(RunControl):
    def __init__(self, budgets: RunBudgets, aggregate: AggregateBudget) -> None:
        super().__init__(budgets)
        self.aggregate = aggregate

    def check(self) -> None:
        super().check()
        try:
            self.aggregate.check_runtime_nowait()
        except AggregateBudgetExceeded as exc:
            raise BudgetExceeded(f"aggregate_{exc.budget}") from exc

    def start_model_step(self) -> int:
        try:
            self.aggregate.consume_model_step_nowait()
        except AggregateBudgetExceeded as exc:
            raise BudgetExceeded(f"aggregate_{exc.budget}") from exc
        return super().start_model_step()

    def reserve_tool_calls(self, count: int) -> None:
        try:
            self.aggregate.consume_tool_calls_nowait(count)
        except AggregateBudgetExceeded as exc:
            raise BudgetExceeded(f"aggregate_{exc.budget}") from exc
        super().reserve_tool_calls(count)


class ChildToolScheduler(ToolScheduler):
    async def execute(
        self,
        calls: tuple[ScheduledCall, ...],
        context: ToolContext,
    ) -> tuple[ScheduledResult, ...]:
        if any(call.tool_name == "ask_user" for call in calls):
            raise AgentBlocked("subagents cannot ask the user directly; clarification is required")
        return await super().execute(calls, context)


class ChildAgentLoop(AgentLoop):
    def __init__(
        self,
        *,
        record: SubagentRecord,
        approval_router: ApprovalRouter,
        **kwargs: Any,
    ) -> None:
        self.subagent_record = record
        self.approval_router = approval_router
        super().__init__(**kwargs)

    async def _approval_handler(
        self,
        request: PolicyRequest,
        decision: PolicyDecision,
    ) -> ApprovalChoice:
        return await self.approval_router.request(
            self.subagent_record.subagent_id,
            self.subagent_record.spec.role,
            request,
            decision,
        )

    async def _request_user(self, payload: object) -> object:
        del payload
        raise AgentBlocked("subagents cannot ask the user directly; clarification is required")


@dataclass(slots=True)
class ChildRuntime:
    record: SubagentRecord
    control: RunControl
    event_bus: EventBus
    loop: AgentLoop
    workspace: Path
    prompt: str


def build_child_prompt(record: SubagentRecord) -> str:
    spec = record.spec
    verification = "\n".join(f"- {item}" for item in spec.verification)
    return (
        f"Task: {spec.task_name}\n"
        f"Goal: {spec.goal}\n\n"
        f"Context:\n{spec.context}\n\n"
        f"Expected output:\n{spec.expected_output}\n\n"
        f"Verification requirements:\n{verification}\n\n"
        "Complete only this task. Do not delegate and do not ask the user questions."
    )


class ChildRuntimeFactory:
    def __init__(
        self,
        *,
        config: AppConfig,
        state_root: Path,
        parent_tools: ToolRegistry,
        model_chain: Callable[[str | None], tuple[ModelTarget, ...]],
        extension_snapshot: ExtensionSnapshot | None = None,
    ) -> None:
        self.config = config
        self.state_root = state_root
        self.parent_tools = parent_tools
        self.model_chain = model_chain
        self.extension_snapshot = extension_snapshot or ExtensionSnapshot(0, "empty")

    def create(
        self,
        record: SubagentRecord,
        *,
        workspace: Path,
        parent_permission: PermissionMode,
        aggregate_budget: AggregateBudget,
        approval_router: ApprovalRouter,
    ) -> ChildRuntime:
        spec = record.spec
        policy = ROLE_POLICIES[spec.role]
        names = resolve_role_tools(
            spec.role,
            spec.kind,
            frozenset(self.parent_tools.names()),
            spec.allowed_tools,
        )
        registry = ToolRegistry()
        for name in self.parent_tools.names():
            if name in names and name != "ask_user" and not name.endswith("_subagent"):
                registry.register(self.parent_tools.get(name))

        sandbox_status = detect_bubblewrap()
        git_common = (
            _git_common_directory(workspace) if spec.kind is SubagentTaskKind.WRITE else None
        )
        sandbox = (
            BubblewrapSandbox(
                workspace,
                sandbox_status,
                read_only_workspace=spec.kind is SubagentTaskKind.READ,
                writable_paths=() if git_common is None else (git_common,),
            )
            if self.config.sandbox.enabled and sandbox_status.available
            else None
        )
        if "shell" in registry.names():
            registry.register(
                ShellTool(
                    sandbox=sandbox,
                    default_timeout=self.config.budgets.shell_timeout_seconds,
                ),
                replace=True,
            )
        effective_permission = parent_permission
        if spec.kind is SubagentTaskKind.READ and sandbox is None:
            effective_permission = PermissionMode.PLAN

        child_session_id = record.child_session_id or uuid4().hex
        child_record = replace(record, child_session_id=child_session_id)
        session = SessionStore.create(self.state_root / "sessions", child_session_id)
        child_run_id = uuid4().hex
        event_bus = EventBus(
            session,
            TraceStore(
                child_run_id,
                root=self.state_root / "traces",
                enabled=self.config.trace.enabled,
                include_tool_arguments=self.config.trace.include_tool_arguments,
                include_transient_events=self.config.trace.include_transient_events,
                retention_days=self.config.trace.retention_days,
                max_total_mb=self.config.trace.max_total_mb,
            ),
        )
        skill_runtime = SkillRuntime(
            SkillCatalog(
                self.extension_snapshot,
                SkillLoader(max_content_bytes=self.config.extensions.max_content_bytes),
            )
        )

        async def activate_skill(selector: str) -> SkillActivationResult:
            result = skill_runtime.activate(selector)
            await event_bus.publish(
                extension_event(
                    event_id=uuid4().hex,
                    session_id=child_session_id,
                    run_id=child_run_id,
                    turn=0,
                    action="skill_loaded",
                    snapshot_generation=self.extension_snapshot.generation,
                    extension_id=result.name,
                    source_id=result.source_id,
                    status="loaded" if result.loaded else "already_loaded",
                ),
                durable=True,
            )
            return result

        if {"search_skills", "load_skill"} <= names:
            register_skill_tools(registry, skill_runtime, activate_skill, replace=True)
        scheduler = ChildToolScheduler(
            registry,
            PolicyEngine(
                effective_permission,
                sandbox_enabled=self.config.sandbox.enabled,
                sandbox_available=sandbox_status.available,
            ),
        )
        budgets = RunBudgets(
            max_model_steps=self.config.subagents.max_model_steps,
            max_tool_calls=self.config.subagents.max_tool_calls,
            max_runtime_seconds=self.config.subagents.max_runtime_seconds,
        )
        control = AggregateRunControl(budgets, aggregate_budget)
        instructions = load_instructions(workspace, workspace_root=workspace)
        system_prompt = build_system_prompt(
            workspace=workspace,
            permission_mode=effective_permission,
            instructions=instructions,
            tools=registry,
            is_subagent=True,
            skills=(skill_runtime.search() if "load_skill" in registry.names() else ()),
            mcp_direct_servers=tuple(
                name.split("__", 1)[0] for name in registry.names() if "__" in name
            ),
        )
        system_prompt += (
            f"\n\n## Temporary subagent role\n{policy.system_instructions}\n"
            "You are a temporary child agent. You cannot delegate or directly ask the user."
        )
        loop = ChildAgentLoop(
            record=child_record,
            approval_router=approval_router,
            session_id=child_session_id,
            run_id=child_run_id,
            model_chain=self.model_chain(spec.model),
            scheduler=scheduler,
            control=control,
            event_bus=event_bus,
            system_prompt=system_prompt,
            token_estimator=TokenEstimator(
                self.config.context.window_tokens,
                compaction_threshold=self.config.context.compaction_threshold,
            ),
            artifact_store=ArtifactStore(session.session_dir),
            preserve_recent_turns=self.config.context.preserve_recent_turns,
            max_tool_result_chars=self.config.context.max_tool_result_chars,
            sourced_context_provider=skill_runtime.drain_context,
        )
        return ChildRuntime(
            child_record,
            control,
            event_bus,
            loop,
            workspace,
            build_child_prompt(child_record),
        )
