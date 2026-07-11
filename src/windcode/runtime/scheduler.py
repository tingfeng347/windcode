from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.policy import (
    ApprovalChoice,
    PolicyAction,
    PolicyDecision,
    PolicyEngine,
    PolicyRequest,
)
from windcode.tools.filesystem import resolve_path
from windcode.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ScheduledCall:
    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ScheduledResult:
    call_id: str
    result: ToolResult


ApprovalHandler = Callable[[PolicyRequest, PolicyDecision], Awaitable[ApprovalChoice]]
BeforeExecute = Callable[[ScheduledCall, PolicyRequest], Awaitable[None]]
SessionApprovalRecorder = Callable[[PolicyRequest], None]


class ToolScheduler:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        *,
        approval_handler: ApprovalHandler | None = None,
        before_execute: BeforeExecute | None = None,
        session_approval_recorder: SessionApprovalRecorder | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.approval_handler = approval_handler
        self.before_execute = before_execute
        self.session_approval_recorder = session_approval_recorder

    def _policy_request(self, call: ScheduledCall, context: ToolContext) -> PolicyRequest:
        tool = self.registry.get(call.tool_name)
        effects = set(tool.effects)
        raw_path = call.arguments.get("path")
        path = str(raw_path) if isinstance(raw_path, str) else None
        if path is not None and not resolve_path(context.workspace, path).inside_workspace:
            effects.add(ToolEffect.OUTSIDE_WORKSPACE)
        if call.tool_name == "shell" and call.arguments.get("network") is True:
            effects.add(ToolEffect.NETWORK)
        command = call.arguments.get("command")
        safe_command = command if isinstance(command, str) else None
        return PolicyRequest(
            request_id=uuid4().hex,
            call_id=call.call_id,
            tool_name=call.tool_name,
            effects=frozenset(effects),
            summary=f"执行工具: {call.tool_name}",
            path=path,
            command=safe_command,
        )

    async def _execute_one(self, call: ScheduledCall, context: ToolContext) -> ScheduledResult:
        try:
            request = self._policy_request(call, context)
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
            if choice is ApprovalChoice.DENY:
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
        if self.before_execute is not None:
            await self.before_execute(call, request)
        result = await self.registry.execute(call.tool_name, context, call.arguments)
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
