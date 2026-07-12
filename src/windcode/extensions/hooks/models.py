from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from windcode.domain.tools import ToolEffect


class HookEvent(StrEnum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_SUBMIT = "user_submit"
    RUN_START = "run_start"
    RUN_END = "run_end"
    RUN_ERROR = "run_error"
    TOOL_BEFORE_POLICY = "tool_before_policy"
    TOOL_AFTER = "tool_after"
    PERMISSION_REQUEST = "permission_request"
    COMPACT_BEFORE = "compact_before"
    COMPACT_AFTER = "compact_after"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_END = "subagent_end"


DECISION_EVENTS = frozenset({HookEvent.TOOL_BEFORE_POLICY})


@dataclass(frozen=True, slots=True)
class HookContext:
    version: int
    event: HookEvent
    session_id: str
    run_id: str
    correlation_id: str
    source_id: str = "windcode"
    tool_id: str | None = None
    status: str | None = None
    fields: tuple[tuple[str, str | int | float | bool | None], ...] = ()


@dataclass(frozen=True, slots=True)
class HookMatcher:
    event: HookEvent
    tool_id: str | None = None
    status: str | None = None

    def matches(self, context: HookContext) -> bool:
        return (
            context.event is self.event
            and (self.tool_id is None or self.tool_id == context.tool_id)
            and (self.status is None or self.status == context.status)
        )


@dataclass(frozen=True, slots=True)
class NotifyAction:
    message: str


@dataclass(frozen=True, slots=True)
class PromptAction:
    content: str


@dataclass(frozen=True, slots=True)
class CommandAction:
    command: str


@dataclass(frozen=True, slots=True)
class RejectAction:
    reason: str


@dataclass(frozen=True, slots=True)
class TightenAction:
    effects: frozenset[ToolEffect]


HookAction = NotifyAction | PromptAction | CommandAction | RejectAction | TightenAction


@dataclass(frozen=True, slots=True)
class HookDefinition:
    hook_id: str
    source_id: str
    matcher: HookMatcher
    action: HookAction
    priority: int = 100
    timeout_seconds: float = 10.0
    output_limit: int = 4096
    required: bool = False

    @property
    def decision_making(self) -> bool:
        return isinstance(self.action, (RejectAction, TightenAction))

    @property
    def sort_key(self) -> tuple[int, str, str]:
        return (self.priority, self.source_id, self.hook_id)


@dataclass(frozen=True, slots=True)
class HookOutcome:
    rejected: str | None = None
    additional_effects: frozenset[ToolEffect] = frozenset()
    notifications: tuple[str, ...] = ()
    sourced_prompts: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[str, ...] = ()
