import asyncio

import pytest

from windcode.domain.events import ApprovalRequested, ApprovalResponse
from windcode.domain.subagents import SubagentRole
from windcode.domain.tools import ToolEffect
from windcode.policy.models import (
    ApprovalChoice,
    PolicyAction,
    PolicyDecision,
    PolicyRequest,
    RiskLevel,
)
from windcode.runtime.subagents.approvals import ApprovalRouter


def request(name: str) -> PolicyRequest:
    return PolicyRequest(
        request_id=name,
        call_id=name,
        tool_name="shell",
        effects=frozenset({ToolEffect.PROCESS}),
        summary=f"run {name}",
        command=name,
    )


def decision() -> PolicyDecision:
    return PolicyDecision(
        action=PolicyAction.ASK,
        risk=RiskLevel.MEDIUM,
        reason="approval required",
        choices=(ApprovalChoice.ALLOW_ONCE, ApprovalChoice.DENY),
    )


async def test_routes_concurrent_responses_to_exact_subagent() -> None:
    events: list[ApprovalRequested] = []
    published = asyncio.Event()

    async def publish(event: ApprovalRequested) -> None:
        events.append(event)
        if len(events) == 2:
            published.set()

    router = ApprovalRouter(parent_session_id="session", parent_run_id="run", publish=publish)
    first = asyncio.create_task(
        router.request("first", SubagentRole.WORKER, request("first"), decision())
    )
    second = asyncio.create_task(
        router.request("second", SubagentRole.VERIFIER, request("second"), decision())
    )
    await published.wait()
    by_child = {event.subagent_id: event for event in events}
    router.respond(ApprovalResponse(by_child["second"].request_id, "deny"))
    router.respond(ApprovalResponse(by_child["first"].request_id, "allow_once"))
    assert await first is ApprovalChoice.ALLOW_ONCE
    assert await second is ApprovalChoice.DENY
    assert router.pending_count == 0


async def test_cancel_only_affects_named_subagent() -> None:
    events: list[ApprovalRequested] = []
    published = asyncio.Event()

    async def publish(event: ApprovalRequested) -> None:
        events.append(event)
        if len(events) == 2:
            published.set()

    router = ApprovalRouter(parent_session_id="session", parent_run_id="run", publish=publish)
    first = asyncio.create_task(
        router.request("first", SubagentRole.WORKER, request("first"), decision())
    )
    second = asyncio.create_task(
        router.request("second", SubagentRole.WORKER, request("second"), decision())
    )
    await published.wait()
    router.cancel("first")
    with pytest.raises(asyncio.CancelledError):
        await first
    second_event = next(event for event in events if event.subagent_id == "second")
    router.respond(ApprovalResponse(second_event.request_id, "allow_once"))
    assert await second is ApprovalChoice.ALLOW_ONCE
