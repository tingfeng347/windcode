from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast
from uuid import uuid4

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.policy import (
    ApprovalChoice,
    CommandAnalysis,
    PolicyAction,
    PolicyDecision,
    PolicyEngine,
    PolicyRequest,
    propose_rule,
)
from windcode.tools.filesystem import resolve_path
from windcode.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ScheduledCall:
    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    origin: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduledResult:
    call_id: str
    result: ToolResult


@dataclass(frozen=True, slots=True)
class PolicyConstraints:
    additional_effects: frozenset[ToolEffect] = frozenset()
    reject_reason: str | None = None


ApprovalHandler = Callable[[PolicyRequest, PolicyDecision], Awaitable[ApprovalChoice]]
BeforeExecute = Callable[[ScheduledCall, PolicyRequest], Awaitable[None]]
BeforePolicy = Callable[[ScheduledCall, ToolContext], Awaitable[PolicyConstraints]]
PermissionObserver = Callable[[ScheduledCall, PolicyRequest, PolicyDecision], Awaitable[None]]
AfterExecute = Callable[[ScheduledCall, PolicyRequest, ToolResult], Awaitable[None]]
SessionApprovalRecorder = Callable[[PolicyRequest], None]


class _DynamicEffects(Protocol):
    def effects_for(self, arguments: Mapping[str, Any]) -> frozenset[ToolEffect]: ...


class _CommandAnalyzer(Protocol):
    def analyze(self, arguments: Mapping[str, Any]) -> CommandAnalysis: ...


class _ApprovalSummarizer(Protocol):
    def approval_summary(self, arguments: Mapping[str, Any]) -> str: ...


class ToolScheduler:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        *,
        approval_handler: ApprovalHandler | None = None,
        before_execute: BeforeExecute | None = None,
        before_policy: BeforePolicy | None = None,
        permission_observer: PermissionObserver | None = None,
        after_execute: AfterExecute | None = None,
        session_approval_recorder: SessionApprovalRecorder | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.approval_handler = approval_handler
        self.before_execute = before_execute
        self.before_policy = before_policy
        self.permission_observer = permission_observer
        self.after_execute = after_execute
        self.session_approval_recorder = session_approval_recorder

    def _policy_request(
        self,
        call: ScheduledCall,
        context: ToolContext,
        additional_effects: frozenset[ToolEffect] = frozenset(),
    ) -> PolicyRequest:
        tool = self.registry.get(call.tool_name)
        dynamic_effects = getattr(tool, "effects_for", None)
        effects: set[ToolEffect] = set(
            cast(_DynamicEffects, tool).effects_for(call.arguments)
            if callable(dynamic_effects)
            else tool.effects
        )
        effects.update(additional_effects)
        raw_path = call.arguments.get("path")
        path = str(raw_path) if isinstance(raw_path, str) else None
        if path is not None and not resolve_path(context.workspace, path).inside_workspace:
            effects.add(ToolEffect.OUTSIDE_WORKSPACE)
        command = call.arguments.get("command")
        safe_command = command if isinstance(command, str) else None
        raw_cwd = call.arguments.get("cwd")
        cwd = raw_cwd if isinstance(raw_cwd, str) else "." if call.tool_name == "shell" else None
        analysis: CommandAnalysis | None = None
        analyzer = getattr(tool, "analyze", None)
        if call.tool_name == "shell" and callable(analyzer):
            analysis = cast(_CommandAnalyzer, tool).analyze(call.arguments)
        network = call.arguments.get("network") is True
        proposed_rule = (
            None
            if analysis is None
            else propose_rule(
                analysis,
                network=network,
                source="project",
                escalated=ToolEffect.OUTSIDE_WORKSPACE in effects,
            )
        )
        sandbox = getattr(tool, "sandbox", None)
        sandbox_policy = getattr(tool, "sandbox_policy", None)
        escalation_reason = call.arguments.get("justification")
        if not isinstance(escalation_reason, str):
            escalation_reason = None
        if ToolEffect.OUTSIDE_WORKSPACE in effects and escalation_reason is None:
            escalation_reason = getattr(getattr(sandbox, "status", None), "warning", None)
            if escalation_reason is None and call.tool_name == "shell":
                escalation_reason = "the system sandbox is disabled or unavailable"
        summarizer = getattr(tool, "approval_summary", None)
        summary = (
            cast(_ApprovalSummarizer, tool).approval_summary(call.arguments)
            if callable(summarizer)
            else f"执行工具: {call.tool_name}"
        )
        return PolicyRequest(
            request_id=uuid4().hex,
            call_id=call.call_id,
            tool_name=call.tool_name,
            effects=frozenset(effects),
            summary=summary,
            path=path,
            command=safe_command,
            cwd=cwd,
            network=network,
            sandbox_backend=getattr(getattr(sandbox, "status", None), "backend", None),
            sandbox_preset=getattr(getattr(sandbox_policy, "preset", None), "value", None),
            escalation_reason=escalation_reason,
            command_analysis=analysis,
            proposed_rule=proposed_rule,
        )

    async def _execute_one(self, call: ScheduledCall, context: ToolContext) -> ScheduledResult:
        try:
            constraints = (
                await self.before_policy(call, context)
                if self.before_policy is not None
                else PolicyConstraints()
            )
            if constraints.reject_reason is not None:
                return ScheduledResult(
                    call.call_id,
                    ToolResult(
                        constraints.reject_reason,
                        is_error=True,
                        data={"error": "extension_rejected"},
                    ),
                )
            request = self._policy_request(call, context, constraints.additional_effects)
        except KeyError as exc:
            return ScheduledResult(
                call.call_id,
                ToolResult(
                    output=str(exc),
                    is_error=True,
                    data={"error": "unknown_tool", "tool": call.tool_name},
                ),
            )
        decision = self.policy.evaluate(request)
        if self.permission_observer is not None:
            await self.permission_observer(call, request, decision)
        if decision.action is PolicyAction.DENY:
            return ScheduledResult(
                call.call_id,
                ToolResult(
                    output=decision.reason,
                    is_error=True,
                    data={"error": "policy_denied", "risk": decision.risk.value},
                ),
            )
        if decision.action is PolicyAction.ASK:
            if self.approval_handler is None:
                return ScheduledResult(
                    call.call_id,
                    ToolResult(
                        output=decision.reason,
                        is_error=True,
                        data={"error": "approval_required", "risk": decision.risk.value},
                    ),
                )
            choice = await self.approval_handler(request, decision)
            if choice in {ApprovalChoice.DENY, ApprovalChoice.CANCEL}:
                return ScheduledResult(
                    call.call_id,
                    ToolResult(
                        output="user denied the operation",
                        is_error=True,
                        data={"error": "approval_denied"},
                    ),
                )
            if choice is ApprovalChoice.ALLOW_SESSION:
                self.policy.approve_for_session(request)
                if self.session_approval_recorder is not None:
                    self.session_approval_recorder(request)
            elif choice is ApprovalChoice.ALLOW_PROJECT:
                self.policy.approve_for_project(request)
        if self.before_execute is not None:
            await self.before_execute(call, request)
        approved_context = replace(context, granted_effects=request.effects)
        result = await self.registry.execute(call.tool_name, approved_context, call.arguments)
        if (
            call.tool_name == "shell"
            and result.data.get("sandbox_denial") is True
            and ToolEffect.OUTSIDE_WORKSPACE not in request.effects
        ):
            escalation = request.model_copy(
                update={
                    "request_id": uuid4().hex,
                    "effects": frozenset({*request.effects, ToolEffect.OUTSIDE_WORKSPACE}),
                    "escalation_reason": "the sandbox denied an operation required by the command",
                    "summary": "沙箱拒绝了命令所需操作, 是否在沙箱外重试",
                    "proposed_rule": (
                        None
                        if request.proposed_rule is None
                        else request.proposed_rule.model_copy(update={"escalated": True})
                    ),
                }
            )
            retry_decision = self.policy.evaluate(escalation)
            if self.permission_observer is not None:
                await self.permission_observer(call, escalation, retry_decision)
            if retry_decision.action is PolicyAction.ASK and self.approval_handler is not None:
                retry_choice = await self.approval_handler(escalation, retry_decision)
                if retry_choice not in {ApprovalChoice.DENY, ApprovalChoice.CANCEL}:
                    if retry_choice is ApprovalChoice.ALLOW_SESSION:
                        self.policy.approve_for_session(escalation)
                        if self.session_approval_recorder is not None:
                            self.session_approval_recorder(escalation)
                    elif retry_choice is ApprovalChoice.ALLOW_PROJECT:
                        self.policy.approve_for_project(escalation)
                    retry_context = replace(context, granted_effects=escalation.effects)
                    result = await self.registry.execute(
                        call.tool_name, retry_context, call.arguments
                    )
        if self.after_execute is not None:
            await self.after_execute(call, request, result)
        return ScheduledResult(call.call_id, result)

    def _is_read_only(self, call: ScheduledCall) -> bool:
        try:
            return self.registry.get(call.tool_name).effects <= {ToolEffect.READ}
        except KeyError:
            return False

    async def execute(
        self,
        calls: tuple[ScheduledCall, ...],
        context: ToolContext,
    ) -> tuple[ScheduledResult, ...]:
        results: list[ScheduledResult] = []
        index = 0
        while index < len(calls):
            if not self._is_read_only(calls[index]):
                results.append(await self._execute_one(calls[index], context))
                index += 1
                continue
            end = index
            while end < len(calls) and self._is_read_only(calls[end]):
                end += 1
            batch = calls[index:end]
            batch_results = await asyncio.gather(
                *(self._execute_one(call, context) for call in batch)
            )
            results.extend(batch_results)
            index = end
        return tuple(results)
