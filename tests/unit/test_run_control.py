import asyncio

import pytest

from windcode.domain.events import ApprovalResponse
from windcode.runtime.control import BudgetExceeded, RunBudgets, RunControl


def test_enforces_model_and_tool_budgets() -> None:
    control = RunControl(RunBudgets(max_model_steps=1, max_tool_calls=2))
    assert control.start_model_step() == 1
    with pytest.raises(BudgetExceeded, match="model_steps"):
        control.start_model_step()
    control.reserve_tool_calls(2)
    with pytest.raises(BudgetExceeded, match="tool_calls"):
        control.reserve_tool_calls(1)


@pytest.mark.asyncio
async def test_response_resolves_matching_waiter() -> None:
    control = RunControl()
    waiter = asyncio.create_task(control.wait_for_response("request"))
    await asyncio.sleep(0)
    response = ApprovalResponse("request", "allow_once")
    control.respond(response)
    assert await waiter == response


@pytest.mark.asyncio
async def test_cancel_releases_all_waiters() -> None:
    control = RunControl()
    waiter = asyncio.create_task(control.wait_for_response("request"))
    await asyncio.sleep(0)
    control.cancel()
    control.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    with pytest.raises(asyncio.CancelledError):
        control.check()


@pytest.mark.asyncio
async def test_wall_clock_budget() -> None:
    control = RunControl(RunBudgets(max_runtime_seconds=0.001))
    await asyncio.sleep(0.01)
    with pytest.raises(BudgetExceeded, match="runtime_seconds"):
        control.check()
