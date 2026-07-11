from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from windcode.domain.events import ApprovalRequested, ApprovalResponse
from windcode.domain.subagents import SubagentRole
from windcode.policy.models import ApprovalChoice, PolicyDecision, PolicyRequest


@dataclass(slots=True)
class _PendingApproval:
    subagent_id: str
    future: asyncio.Future[ApprovalChoice]


class ApprovalRouter:
    def __init__(
        self,
        *,
        parent_session_id: str,
        parent_run_id: str,
        publish: Callable[[ApprovalRequested], Awaitable[None]],
    ) -> None:
        self.parent_session_id = parent_session_id
        self.parent_run_id = parent_run_id
        self.publish = publish
        self._pending: dict[str, _PendingApproval] = {}

    async def request(
        self,
        subagent_id: str,
        role: SubagentRole,
        request: PolicyRequest,
        decision: PolicyDecision,
    ) -> ApprovalChoice:
        parent_request_id = uuid4().hex
        future: asyncio.Future[ApprovalChoice] = asyncio.get_running_loop().create_future()
        self._pending[parent_request_id] = _PendingApproval(subagent_id, future)
        arguments_summary = request.command or request.path or request.summary
        try:
            await self.publish(
                ApprovalRequested(
                    event_id=uuid4().hex,
                    session_id=self.parent_session_id,
                    run_id=self.parent_run_id,
                    turn=0,
                    request_id=parent_request_id,
                    summary=request.summary,
                    risk=decision.risk.value,
                    choices=tuple(choice.value for choice in decision.choices),
                    subagent_id=subagent_id,
                    subagent_role=role.value,
                    tool_name=request.tool_name,
                    arguments_summary=arguments_summary,
                )
            )
            return await future
        finally:
            self._pending.pop(parent_request_id, None)

    def respond(self, response: ApprovalResponse) -> None:
        try:
            pending = self._pending[response.request_id]
        except KeyError as exc:
            raise ValueError(f"no pending subagent approval: {response.request_id}") from exc
        try:
            choice = ApprovalChoice(response.decision)
        except ValueError:
            choice = ApprovalChoice.DENY
        if not pending.future.done():
            pending.future.set_result(choice)

    def cancel(self, subagent_id: str) -> None:
        for pending in tuple(self._pending.values()):
            if pending.subagent_id == subagent_id and not pending.future.done():
                pending.future.cancel()

    @property
    def pending_count(self) -> int:
        return len(self._pending)
