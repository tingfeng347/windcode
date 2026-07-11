from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from windcode.domain.events import RunResponse


@dataclass(frozen=True, slots=True)
class RunBudgets:
    max_model_steps: int = 40
    max_tool_calls: int = 100
    max_runtime_seconds: float = 1800.0


class BudgetExceeded(RuntimeError):
    def __init__(self, budget: str) -> None:
        self.budget = budget
        super().__init__(f"run budget exhausted: {budget}")


class RunControl:
    def __init__(self, budgets: RunBudgets | None = None) -> None:
        self.budgets = budgets or RunBudgets()
        self.started_at = monotonic()
        self.model_steps = 0
        self.tool_calls = 0
        self._cancelled = asyncio.Event()
        self._compaction_requested = False
        self._pending: dict[str, asyncio.Future[RunResponse]] = {}

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError
        if monotonic() - self.started_at >= self.budgets.max_runtime_seconds:
            raise BudgetExceeded("runtime_seconds")

    def start_model_step(self) -> int:
        self.check()
        if self.model_steps >= self.budgets.max_model_steps:
            raise BudgetExceeded("model_steps")
        self.model_steps += 1
        return self.model_steps

    def reserve_tool_calls(self, count: int) -> None:
        self.check()
        if self.tool_calls + count > self.budgets.max_tool_calls:
            raise BudgetExceeded("tool_calls")
        self.tool_calls += count

    async def wait_for_response(self, request_id: str) -> RunResponse:
        self.check()
        if request_id in self._pending:
            raise ValueError(f"duplicate pending request: {request_id}")
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            return await future
        finally:
            self._pending.pop(request_id, None)

    def respond(self, response: RunResponse) -> None:
        try:
            future = self._pending[response.request_id]
        except KeyError as exc:
            raise ValueError(f"no pending request: {response.request_id}") from exc
        if not future.done():
            future.set_result(response)

    def cancel(self) -> None:
        if self.cancelled:
            return
        self._cancelled.set()
        for future in tuple(self._pending.values()):
            if not future.done():
                future.cancel()

    def request_compaction(self) -> None:
        self._compaction_requested = True

    def consume_compaction_request(self) -> bool:
        requested = self._compaction_requested
        self._compaction_requested = False
        return requested
