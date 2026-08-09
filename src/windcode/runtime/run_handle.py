from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from windcode.config import PermissionMode
from windcode.domain.events import AgentEventType, ApprovalResponse, RunResponse, RunResult
from windcode.domain.subagents import SubagentRecord, SubagentResult
from windcode.policy import PolicyEngine
from windcode.runtime.control import RunControl
from windcode.runtime.event_bus import EventBus
from windcode.runtime.loop import AgentLoop
from windcode.runtime.subagents.coordinator import SubagentCoordinator


class RunHandle:
    def __init__(
        self,
        task: asyncio.Task[RunResult],
        event_bus: EventBus,
        control: RunControl,
        *,
        after_sequence: int = 0,
        coordinator: SubagentCoordinator,
        policy: PolicyEngine,
        loop: AgentLoop,
    ) -> None:
        self._task = task
        self._event_bus = event_bus
        self._control = control
        self._after_sequence = after_sequence
        self._coordinator = coordinator
        self._policy = policy
        self._loop = loop
        self._result: RunResult | None = None
        self._result_lock = asyncio.Lock()

    def __aiter__(self) -> AsyncIterator[AgentEventType]:
        return self._event_bus.subscribe(after_sequence=self._after_sequence)

    async def respond(self, response: RunResponse) -> None:
        try:
            self._control.respond(response)
        except ValueError:
            if not isinstance(response, ApprovalResponse):
                raise
            self._coordinator.approvals.respond(response)

    async def cancel(self) -> None:
        self._control.cancel()
        if not self._task.done():
            self._task.cancel()
        try:
            await self.result()
        finally:
            # The run wrapper normally performs this cleanup after publishing
            # RunCancelled. Keep the explicit shutdown as an idempotent safety
            # net for cancellation before the wrapper coroutine starts.
            await self._coordinator.shutdown("parent run cancelled")

    async def result(self) -> RunResult:
        if self._result is not None:
            return self._result
        async with self._result_lock:
            if self._result is None:
                self._result = await self._task
            return self._result

    async def compact(self) -> None:
        if self.done:
            raise RuntimeError("cannot compact a completed run")
        self._control.request_compaction()

    @property
    def permission_mode(self) -> PermissionMode:
        return self._policy.mode

    def set_permission_mode(self, mode: PermissionMode | str) -> PermissionMode:
        selected = PermissionMode(mode)
        previous = self._policy.mode
        self._policy.set_mode(selected)
        self._loop.system_prompt = self._loop.system_prompt.replace(
            f"权限模式: {previous.value}.",
            f"权限模式: {selected.value}.",
        )
        self._coordinator.set_permission_mode(selected)
        return selected

    @property
    def done(self) -> bool:
        return self._task.done()

    def add_done_callback(self, callback: Callable[[RunHandle], None]) -> None:
        self._task.add_done_callback(lambda _task: callback(self))

    def subagents(self) -> tuple[SubagentRecord, ...]:
        return self._coordinator.list()

    async def cancel_subagent(self, subagent_id: str) -> None:
        if self.done:
            raise RuntimeError("cannot cancel a subagent after the parent run has ended")
        await self._coordinator.cancel(subagent_id)

    async def integrate_subagent(
        self,
        subagent_id: str,
        *,
        verification_commands: tuple[str, ...] = (),
    ) -> SubagentResult:
        if self.done:
            raise RuntimeError("cannot integrate a subagent after the parent run has ended")
        return await self._coordinator.integrate(subagent_id, verification_commands)
