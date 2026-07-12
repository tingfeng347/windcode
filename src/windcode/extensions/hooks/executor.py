from __future__ import annotations

from collections.abc import Awaitable, Callable

from windcode.domain.messages import SourcedContextMessage
from windcode.extensions.hooks.models import (
    HookContext,
    HookDefinition,
    HookOutcome,
    NotifyAction,
    PromptAction,
    RejectAction,
    TightenAction,
)

CommandRunner = Callable[[str, str, HookContext], Awaitable[str]]


class HookExecutor:
    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self.command_runner = command_runner
        self.notifications: list[tuple[str, str]] = []
        self.context_messages: list[SourcedContextMessage] = []

    def drain_context(self) -> tuple[SourcedContextMessage, ...]:
        messages = tuple(self.context_messages)
        self.context_messages.clear()
        return messages

    async def execute(self, hook: HookDefinition, context: HookContext) -> HookOutcome:
        action = hook.action
        if isinstance(action, NotifyAction):
            message = action.message[: hook.output_limit]
            self.notifications.append((hook.source_id, message))
            return HookOutcome(notifications=(message,))
        if isinstance(action, PromptAction):
            content = action.content[: hook.output_limit]
            self.context_messages.append(SourcedContextMessage(hook.source_id, content))
            return HookOutcome(sourced_prompts=((hook.source_id, content),))
        if isinstance(action, RejectAction):
            return HookOutcome(rejected=action.reason[: hook.output_limit])
        if isinstance(action, TightenAction):
            return HookOutcome(additional_effects=action.effects)
        if self.command_runner is None:
            raise RuntimeError("Hook command runner is not configured")
        output = await self.command_runner(
            action.command, f"hook:{hook.source_id}/{hook.hook_id}", context
        )
        return HookOutcome(notifications=(output[: hook.output_limit],))
