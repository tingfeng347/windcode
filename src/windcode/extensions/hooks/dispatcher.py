from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from windcode.domain.tools import ToolEffect
from windcode.extensions.hooks.executor import HookExecutor
from windcode.extensions.hooks.models import HookContext, HookDefinition, HookOutcome

HookObserver = Callable[[str, HookDefinition, HookContext, HookOutcome | None], Awaitable[None]]


class HookDispatcher:
    def __init__(
        self,
        hooks: tuple[HookDefinition, ...],
        executor: HookExecutor,
        observer: HookObserver | None = None,
    ) -> None:
        self.hooks = tuple(sorted(hooks, key=lambda item: item.sort_key))
        self.executor = executor
        self._active_sources: set[str] = set()
        self._background: set[asyncio.Task[HookOutcome]] = set()
        self._closed = False
        self.observer = observer

    async def dispatch(self, context: HookContext, *, background: bool = False) -> HookOutcome:
        if self._closed:
            raise RuntimeError("Hook dispatcher is closed")
        matched = tuple(
            hook
            for hook in self.hooks
            if hook.matcher.matches(context)
            and context.source_id != f"hook:{hook.source_id}/{hook.hook_id}"
        )
        if background:
            required = tuple(hook for hook in matched if hook.required)
            for hook in required:
                try:
                    await self._run_one(hook, context)
                except (TimeoutError, RuntimeError, ValueError, OSError) as exc:
                    raise RuntimeError(
                        f"required Hook failed: {hook.source_id}/{hook.hook_id}: {exc}"
                    ) from exc
            for hook in matched:
                if hook.required:
                    continue
                task = asyncio.create_task(self._run_one(hook, context))
                self._background.add(task)
            return HookOutcome()
        rejected: str | None = None
        effects: set[ToolEffect] = set()
        notifications: list[str] = []
        prompts: list[tuple[str, str]] = []
        diagnostics: list[str] = []
        for hook in matched:
            try:
                outcome = await self._run_one(hook, context)
            except (TimeoutError, RuntimeError, ValueError, OSError) as exc:
                diagnostics.append(f"{hook.source_id}/{hook.hook_id}: {exc}")
                if hook.required:
                    raise RuntimeError(
                        f"required Hook failed: {hook.source_id}/{hook.hook_id}: {exc}"
                    ) from exc
                if hook.decision_making:
                    rejected = rejected or "security Hook failed closed"
                continue
            rejected = rejected or outcome.rejected
            effects.update(outcome.additional_effects)
            notifications.extend(outcome.notifications)
            prompts.extend(outcome.sourced_prompts)
            diagnostics.extend(outcome.diagnostics)
        return HookOutcome(
            rejected,
            frozenset(effects),
            tuple(notifications),
            tuple(prompts),
            tuple(diagnostics),
        )

    async def _run_one(self, hook: HookDefinition, context: HookContext) -> HookOutcome:
        recursion_key = f"{hook.source_id}/{hook.hook_id}"
        if recursion_key in self._active_sources:
            raise RuntimeError("recursive Hook invocation blocked")
        self._active_sources.add(recursion_key)
        try:
            if self.observer is not None:
                await self.observer("started", hook, context, None)
            async with asyncio.timeout(hook.timeout_seconds):
                outcome = await self.executor.execute(hook, context)
            if self.observer is not None:
                await self.observer(
                    "rejected" if outcome.rejected is not None else "finished",
                    hook,
                    context,
                    outcome,
                )
            return outcome
        finally:
            self._active_sources.discard(recursion_key)

    async def aclose(self) -> None:
        self._closed = True
        tasks = tuple(self._background)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background.clear()
