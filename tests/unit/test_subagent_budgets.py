import asyncio

import pytest

from windcode.runtime.control import BudgetExceeded, RunBudgets
from windcode.runtime.subagents.budgets import AggregateBudget, AggregateBudgetExceeded
from windcode.runtime.subagents.factory import AggregateRunControl


async def test_concurrent_consumption_stops_exactly_at_limit() -> None:
    budget = AggregateBudget(max_model_steps=3, max_tool_calls=5, max_runtime_seconds=60)
    results = await asyncio.gather(
        *(budget.consume_model_step() for _ in range(4)), return_exceptions=True
    )
    assert sum(isinstance(result, AggregateBudgetExceeded) for result in results) == 1
    assert (await budget.usage()).model_steps == 3


async def test_tool_consumption_is_not_returned_after_rejection() -> None:
    budget = AggregateBudget(max_model_steps=1, max_tool_calls=2, max_runtime_seconds=60)
    await budget.consume_tool_calls(2)
    with pytest.raises(AggregateBudgetExceeded, match="tool_calls"):
        await budget.consume_tool_calls()
    assert (await budget.usage()).tool_calls == 2


async def test_budget_errors_identify_aggregate_scope() -> None:
    budget = AggregateBudget(max_model_steps=1, max_tool_calls=1, max_runtime_seconds=60)
    await budget.consume_model_step()
    with pytest.raises(AggregateBudgetExceeded) as error:
        await budget.consume_model_step()
    assert (error.value.scope, error.value.budget) == ("aggregate", "model_steps")


async def test_runtime_budget_is_checked() -> None:
    budget = AggregateBudget(max_model_steps=1, max_tool_calls=1, max_runtime_seconds=0.001)
    await asyncio.sleep(0.002)
    with pytest.raises(AggregateBudgetExceeded, match="runtime_seconds"):
        await budget.check_runtime()


def test_child_and_aggregate_model_budgets_have_distinct_errors() -> None:
    child_aggregate = AggregateBudget(max_model_steps=10, max_tool_calls=4, max_runtime_seconds=60)
    first = AggregateRunControl(RunBudgets(max_model_steps=1), child_aggregate)

    first.start_model_step()
    with pytest.raises(BudgetExceeded, match="model_steps") as child_error:
        first.start_model_step()
    assert "aggregate" not in str(child_error.value)

    shared = AggregateBudget(max_model_steps=1, max_tool_calls=4, max_runtime_seconds=60)
    second = AggregateRunControl(RunBudgets(max_model_steps=2), shared)
    third = AggregateRunControl(RunBudgets(max_model_steps=2), shared)
    second.start_model_step()
    with pytest.raises(BudgetExceeded, match="aggregate_model_steps"):
        third.start_model_step()


def test_child_and_aggregate_tool_budgets_have_distinct_errors() -> None:
    child_aggregate = AggregateBudget(max_model_steps=4, max_tool_calls=10, max_runtime_seconds=60)
    first = AggregateRunControl(RunBudgets(max_tool_calls=1), child_aggregate)

    first.reserve_tool_calls(1)
    with pytest.raises(BudgetExceeded, match="tool_calls") as child_error:
        first.reserve_tool_calls(1)
    assert "aggregate" not in str(child_error.value)

    shared = AggregateBudget(max_model_steps=4, max_tool_calls=1, max_runtime_seconds=60)
    second = AggregateRunControl(RunBudgets(max_tool_calls=2), shared)
    third = AggregateRunControl(RunBudgets(max_tool_calls=2), shared)
    second.reserve_tool_calls(1)
    with pytest.raises(BudgetExceeded, match="aggregate_tool_calls"):
        third.reserve_tool_calls(1)
