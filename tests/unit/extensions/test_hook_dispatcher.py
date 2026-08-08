import asyncio

import pytest

from windcode.domain.errors import ErrorCategory, RequiredExtensionError
from windcode.extensions.hooks.dispatcher import HookDispatcher
from windcode.extensions.hooks.executor import HookExecutor
from windcode.extensions.hooks.models import (
    HookContext,
    HookDefinition,
    HookEvent,
    HookMatcher,
    HookOutcome,
    NotifyAction,
    RejectAction,
)


def _context() -> HookContext:
    return HookContext(1, HookEvent.TOOL_BEFORE_POLICY, "s", "r", "c", tool_id="shell")


@pytest.mark.asyncio
async def test_pre_hooks_are_ordered_and_rejection_is_sticky() -> None:
    hooks = (
        HookDefinition(
            "notify", "b", HookMatcher(HookEvent.TOOL_BEFORE_POLICY), NotifyAction("ok"), priority=2
        ),
        HookDefinition(
            "reject", "a", HookMatcher(HookEvent.TOOL_BEFORE_POLICY), RejectAction("no"), priority=1
        ),
    )
    dispatcher = HookDispatcher(hooks, HookExecutor())
    outcome = await dispatcher.dispatch(_context())
    assert outcome.rejected == "no"
    assert outcome.notifications == ("ok",)


@pytest.mark.asyncio
async def test_decision_hook_timeout_fails_closed() -> None:
    class SlowExecutor(HookExecutor):
        async def execute(self, hook: HookDefinition, context: HookContext):  # type: ignore[no-untyped-def]
            await asyncio.sleep(1)
            return await super().execute(hook, context)

    hook = HookDefinition(
        "guard",
        "x",
        HookMatcher(HookEvent.TOOL_BEFORE_POLICY),
        RejectAction("no"),
        timeout_seconds=0.001,
    )
    outcome = await HookDispatcher((hook,), SlowExecutor()).dispatch(_context())
    assert outcome.rejected == "security Hook failed closed"


@pytest.mark.asyncio
async def test_required_observer_failure_propagates_and_optional_background_closes() -> None:
    class RecordingExecutor(HookExecutor):
        def __init__(self, fail_sources: set[str]) -> None:
            super().__init__()
            self.fail_sources = fail_sources
            self.calls: list[str] = []

        async def execute(self, hook: HookDefinition, context: HookContext) -> HookOutcome:
            self.calls.append(hook.source_id)
            if hook.source_id in self.fail_sources:
                raise ValueError("failed")
            return await super().execute(hook, context)

    required = HookDefinition(
        "required",
        "plugin:test/required",
        HookMatcher(HookEvent.TOOL_AFTER),
        NotifyAction("required"),
        required=True,
    )
    optional = HookDefinition(
        "optional",
        "plugin:test/optional",
        HookMatcher(HookEvent.TOOL_AFTER),
        NotifyAction("optional"),
    )
    executor = RecordingExecutor(fail_sources={required.source_id})
    dispatcher = HookDispatcher((required, optional), executor)
    context = HookContext(1, HookEvent.TOOL_AFTER, "session", "run", "call")

    with pytest.raises(RequiredExtensionError, match="required Hook failed") as error:
        await dispatcher.dispatch(context, background=True)
    assert isinstance(error.value, RuntimeError)
    assert error.value.category is ErrorCategory.EXTENSION
    assert error.value.failed_sources == ("plugin:test/required/required",)
    await dispatcher.aclose()

    assert optional.source_id not in executor.calls

    optional_executor = RecordingExecutor(set())
    optional_dispatcher = HookDispatcher((optional,), optional_executor)
    await optional_dispatcher.dispatch(context, background=True)
    await optional_dispatcher.aclose()
    assert optional_executor.calls == [optional.source_id]
