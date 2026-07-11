from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic


class AggregateBudgetExceeded(RuntimeError):
    def __init__(self, budget: str) -> None:
        self.scope = "aggregate"
        self.budget = budget
        super().__init__(f"aggregate subagent budget exhausted: {budget}")


@dataclass(frozen=True, slots=True)
class AggregateUsage:
    model_steps: int
    tool_calls: int
    runtime_seconds: float


class AggregateBudget:
    def __init__(
        self,
        *,
        max_model_steps: int,
        max_tool_calls: int,
        max_runtime_seconds: float,
    ) -> None:
        self.max_model_steps = max_model_steps
        self.max_tool_calls = max_tool_calls
        self.max_runtime_seconds = max_runtime_seconds
        self._model_steps = 0
        self._tool_calls = 0
        self._started_at = monotonic()
        self._lock = asyncio.Lock()

    async def consume_model_step(self) -> None:
        async with self._lock:
            self.consume_model_step_nowait()

    async def consume_tool_calls(self, count: int = 1) -> None:
        if count < 1:
            raise ValueError("tool call count must be positive")
        async with self._lock:
            self.consume_tool_calls_nowait(count)

    async def check_runtime(self) -> None:
        async with self._lock:
            self._check_runtime_unlocked()

    async def usage(self) -> AggregateUsage:
        async with self._lock:
            return AggregateUsage(
                model_steps=self._model_steps,
                tool_calls=self._tool_calls,
                runtime_seconds=max(0.0, monotonic() - self._started_at),
            )

    def _check_runtime_unlocked(self) -> None:
        if monotonic() - self._started_at >= self.max_runtime_seconds:
            raise AggregateBudgetExceeded("runtime_seconds")

    def check_runtime_nowait(self) -> None:
        self._check_runtime_unlocked()

    def consume_model_step_nowait(self) -> None:
        self._check_runtime_unlocked()
        if self._model_steps >= self.max_model_steps:
            raise AggregateBudgetExceeded("model_steps")
        self._model_steps += 1

    def consume_tool_calls_nowait(self, count: int = 1) -> None:
        if count < 1:
            raise ValueError("tool call count must be positive")
        self._check_runtime_unlocked()
        if self._tool_calls + count > self.max_tool_calls:
            raise AggregateBudgetExceeded("tool_calls")
        self._tool_calls += count
