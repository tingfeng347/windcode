from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, cast

from windcode.domain.models import Usage
from windcode.domain.tools import ToolResult


@dataclass(frozen=True, slots=True)
class RunRequest:
    prompt: str
    workspace: Path
    session_id: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    compact_before_run: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    request_id: str
    decision: str


@dataclass(frozen=True, slots=True)
class UserResponse:
    request_id: str
    answers: dict[str, str]


RunResponse = ApprovalResponse | UserResponse


@dataclass(frozen=True, slots=True)
class RunResult:
    status: str
    final_text: str = ""
    changed_files: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    session_id: str
    run_id: str
    turn: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sequence: int | None = None
    kind: ClassVar[str] = "event"


@dataclass(frozen=True, slots=True)
class RunStarted(AgentEvent):
    kind: ClassVar[str] = "run_started"
    prompt: str = ""


@dataclass(frozen=True, slots=True)
class ModelStarted(AgentEvent):
    kind: ClassVar[str] = "model_started"
    model: str = ""


@dataclass(frozen=True, slots=True)
class TextDeltaEvent(AgentEvent):
    kind: ClassVar[str] = "text_delta"
    text: str = ""


@dataclass(frozen=True, slots=True)
class ReasoningStatus(AgentEvent):
    kind: ClassVar[str] = "reasoning_status"
    status: str = ""


@dataclass(frozen=True, slots=True)
class ToolStarted(AgentEvent):
    kind: ClassVar[str] = "tool_started"
    call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class ToolProgress(AgentEvent):
    kind: ClassVar[str] = "tool_progress"
    call_id: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class ToolFinished(AgentEvent):
    kind: ClassVar[str] = "tool_finished"
    call_id: str = ""
    result: ToolResult = field(default_factory=lambda: ToolResult(output=""))


@dataclass(frozen=True, slots=True)
class ApprovalRequested(AgentEvent):
    kind: ClassVar[str] = "approval_requested"
    request_id: str = ""
    summary: str = ""
    risk: str = ""
    choices: tuple[str, ...] = ()
    subagent_id: str | None = None
    subagent_role: str | None = None
    tool_name: str | None = None
    arguments_summary: str | None = None


@dataclass(frozen=True, slots=True)
class UserInputRequested(AgentEvent):
    kind: ClassVar[str] = "user_input_requested"
    request_id: str = ""
    questions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class UsageUpdated(AgentEvent):
    kind: ClassVar[str] = "usage_updated"
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class ModelRetrying(AgentEvent):
    kind: ClassVar[str] = "model_retrying"
    model: str = ""
    attempt: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ModelFallback(AgentEvent):
    kind: ClassVar[str] = "model_fallback"
    from_model: str = ""
    to_model: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ContextCompacted(AgentEvent):
    kind: ClassVar[str] = "context_compacted"
    before_tokens: int = 0
    after_tokens: int = 0


@dataclass(frozen=True, slots=True)
class RunCompleted(AgentEvent):
    kind: ClassVar[str] = "run_completed"
    result: RunResult = field(default_factory=lambda: RunResult(status="completed"))


@dataclass(frozen=True, slots=True)
class RunFailed(AgentEvent):
    kind: ClassVar[str] = "run_failed"
    message: str = ""
    category: str = "internal"


@dataclass(frozen=True, slots=True)
class RunCancelled(AgentEvent):
    kind: ClassVar[str] = "run_cancelled"
    reason: str = "cancelled by user"


@dataclass(frozen=True, slots=True)
class ExtensionEvent(AgentEvent):
    kind: ClassVar[str] = "extension_event"
    action: str = "diagnostic"
    snapshot_generation: int = 0
    extension_id: str = ""
    source_id: str = ""
    server_id: str | None = None
    hook_id: str | None = None
    call_id: str | None = None
    status: str = ""
    details: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class SubagentEvent(AgentEvent):
    parent_run_id: str = ""
    subagent_id: str = ""
    task_index: int = 0
    role: str = ""
    task_name: str = ""
    summary: str = ""


@dataclass(frozen=True, slots=True)
class SubagentQueued(SubagentEvent):
    kind: ClassVar[str] = "subagent_queued"


@dataclass(frozen=True, slots=True)
class SubagentStarted(SubagentEvent):
    kind: ClassVar[str] = "subagent_started"
    workspace: str = ""


@dataclass(frozen=True, slots=True)
class SubagentProgress(SubagentEvent):
    kind: ClassVar[str] = "subagent_progress"
    activity: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class SubagentBlocked(SubagentEvent):
    kind: ClassVar[str] = "subagent_blocked"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SubagentCompleted(SubagentEvent):
    kind: ClassVar[str] = "subagent_completed"
    commit: str | None = None
    changed_files: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class SubagentFailed(SubagentEvent):
    kind: ClassVar[str] = "subagent_failed"
    message: str = ""
    category: str = "internal"
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class SubagentCancelled(SubagentEvent):
    kind: ClassVar[str] = "subagent_cancelled"
    reason: str = "cancelled"
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class SubagentIntegrated(SubagentEvent):
    kind: ClassVar[str] = "subagent_integrated"
    commit: str = ""
    verification: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SubagentConflict(SubagentEvent):
    kind: ClassVar[str] = "subagent_conflict"
    conflict_files: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True, slots=True)
class SubagentCleanup(SubagentEvent):
    kind: ClassVar[str] = "subagent_cleanup"
    removed: bool = False
    retained_path: str | None = None
    reason: str | None = None


AgentEventType = (
    RunStarted
    | ModelStarted
    | TextDeltaEvent
    | ReasoningStatus
    | ToolStarted
    | ToolProgress
    | ToolFinished
    | ApprovalRequested
    | UserInputRequested
    | UsageUpdated
    | ModelRetrying
    | ModelFallback
    | ContextCompacted
    | RunCompleted
    | RunFailed
    | RunCancelled
    | ExtensionEvent
    | SubagentQueued
    | SubagentStarted
    | SubagentProgress
    | SubagentBlocked
    | SubagentCompleted
    | SubagentFailed
    | SubagentCancelled
    | SubagentIntegrated
    | SubagentConflict
    | SubagentCleanup
)


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        raw = cast(dict[str, object], asdict(value))
        return {key: _json_value(item) for key, item in raw.items()}
    if isinstance(value, dict):
        raw_mapping = cast(dict[object, object], value)
        return {str(key): _json_value(item) for key, item in raw_mapping.items()}
    if isinstance(value, (tuple, list)):
        raw_sequence = cast(list[object] | tuple[object, ...], value)
        return [_json_value(item) for item in raw_sequence]
    return value


def event_to_dict(event: AgentEventType) -> dict[str, Any]:
    payload = cast(dict[str, Any], _json_value(event))
    return {"kind": event.kind, **payload}


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    raw = cast(Mapping[object, object], value)
    return {str(key): item for key, item in raw.items()}


def _int_value(value: object, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return int(value)
    return default


def _float_value(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return default


def _usage(value: object) -> Usage:
    raw = _mapping(value)
    return Usage(
        input_tokens=_int_value(raw.get("input_tokens")),
        output_tokens=_int_value(raw.get("output_tokens")),
        cache_read_tokens=_int_value(raw.get("cache_read_tokens")),
        cache_write_tokens=_int_value(raw.get("cache_write_tokens")),
    )


def _common(raw: Mapping[str, object]) -> dict[str, Any]:
    created = raw.get("created_at")
    sequence = raw.get("sequence")
    return {
        "event_id": str(raw["event_id"]),
        "session_id": str(raw["session_id"]),
        "run_id": str(raw["run_id"]),
        "turn": _int_value(raw["turn"]),
        "created_at": (
            datetime.fromisoformat(created) if isinstance(created, str) else datetime.now(UTC)
        ),
        "sequence": int(sequence) if isinstance(sequence, int) else None,
    }


def _subagent_common(raw: Mapping[str, object]) -> dict[str, Any]:
    return {
        **_common(raw),
        "parent_run_id": str(raw.get("parent_run_id", "")),
        "subagent_id": str(raw.get("subagent_id", "")),
        "task_index": _int_value(raw.get("task_index")),
        "role": str(raw.get("role", "")),
        "task_name": str(raw.get("task_name", "")),
        "summary": str(raw.get("summary", "")),
    }


def event_from_dict(value: Mapping[str, object]) -> AgentEventType:
    raw = {str(key): item for key, item in value.items()}
    kind = str(raw.get("kind", ""))
    common = _common(raw)
    if kind == RunStarted.kind:
        return RunStarted(**common, prompt=str(raw.get("prompt", "")))
    if kind == ModelStarted.kind:
        return ModelStarted(**common, model=str(raw.get("model", "")))
    if kind == TextDeltaEvent.kind:
        return TextDeltaEvent(**common, text=str(raw.get("text", "")))
    if kind == ReasoningStatus.kind:
        return ReasoningStatus(**common, status=str(raw.get("status", "")))
    if kind == ToolStarted.kind:
        return ToolStarted(
            **common,
            call_id=str(raw.get("call_id", "")),
            tool_name=str(raw.get("tool_name", "")),
            arguments=_mapping(raw.get("arguments")),
        )
    if kind == ToolProgress.kind:
        return ToolProgress(
            **common,
            call_id=str(raw.get("call_id", "")),
            message=str(raw.get("message", "")),
        )
    if kind == ToolFinished.kind:
        result = _mapping(raw.get("result"))
        return ToolFinished(
            **common,
            call_id=str(raw.get("call_id", "")),
            result=ToolResult(
                output=str(result.get("output", "")),
                is_error=bool(result.get("is_error", False)),
                artifact_ref=(
                    None if result.get("artifact_ref") is None else str(result.get("artifact_ref"))
                ),
                elapsed_seconds=_float_value(result.get("elapsed_seconds")),
                data=_mapping(result.get("data")),
            ),
        )
    if kind == ApprovalRequested.kind:
        choices = raw.get("choices", ())
        return ApprovalRequested(
            **common,
            request_id=str(raw.get("request_id", "")),
            summary=str(raw.get("summary", "")),
            risk=str(raw.get("risk", "")),
            choices=tuple(str(item) for item in cast(list[object] | tuple[object, ...], choices)),
            subagent_id=(None if raw.get("subagent_id") is None else str(raw.get("subagent_id"))),
            subagent_role=(
                None if raw.get("subagent_role") is None else str(raw.get("subagent_role"))
            ),
            tool_name=None if raw.get("tool_name") is None else str(raw.get("tool_name")),
            arguments_summary=(
                None if raw.get("arguments_summary") is None else str(raw.get("arguments_summary"))
            ),
        )
    if kind == UserInputRequested.kind:
        questions = raw.get("questions", ())
        return UserInputRequested(
            **common,
            request_id=str(raw.get("request_id", "")),
            questions=tuple(
                _mapping(item) for item in cast(list[object] | tuple[object, ...], questions)
            ),
        )
    if kind == UsageUpdated.kind:
        return UsageUpdated(**common, usage=_usage(raw.get("usage")))
    if kind == ModelRetrying.kind:
        return ModelRetrying(
            **common,
            model=str(raw.get("model", "")),
            attempt=_int_value(raw.get("attempt")),
            reason=str(raw.get("reason", "")),
        )
    if kind == ModelFallback.kind:
        return ModelFallback(
            **common,
            from_model=str(raw.get("from_model", "")),
            to_model=str(raw.get("to_model", "")),
            reason=str(raw.get("reason", "")),
        )
    if kind == ContextCompacted.kind:
        return ContextCompacted(
            **common,
            before_tokens=_int_value(raw.get("before_tokens")),
            after_tokens=_int_value(raw.get("after_tokens")),
        )
    if kind == RunCompleted.kind:
        result = _mapping(raw.get("result"))
        changed = result.get("changed_files", ())
        verification = result.get("verification", ())
        return RunCompleted(
            **common,
            result=RunResult(
                status=str(result.get("status", "completed")),
                final_text=str(result.get("final_text", "")),
                changed_files=tuple(
                    str(item) for item in cast(list[object] | tuple[object, ...], changed)
                ),
                verification=tuple(
                    str(item) for item in cast(list[object] | tuple[object, ...], verification)
                ),
                usage=_usage(result.get("usage")),
            ),
        )
    if kind == RunFailed.kind:
        return RunFailed(
            **common,
            message=str(raw.get("message", "")),
            category=str(raw.get("category", "internal")),
        )
    if kind == RunCancelled.kind:
        return RunCancelled(**common, reason=str(raw.get("reason", "cancelled by user")))
    if kind == ExtensionEvent.kind:
        return ExtensionEvent(
            **common,
            action=str(raw.get("action", "diagnostic")),
            snapshot_generation=_int_value(raw.get("snapshot_generation")),
            extension_id=str(raw.get("extension_id", "")),
            source_id=str(raw.get("source_id", "")),
            server_id=None if raw.get("server_id") is None else str(raw.get("server_id")),
            hook_id=None if raw.get("hook_id") is None else str(raw.get("hook_id")),
            call_id=None if raw.get("call_id") is None else str(raw.get("call_id")),
            status=str(raw.get("status", "")),
            details=_mapping(raw.get("details")),
        )
    if kind == SubagentQueued.kind:
        return SubagentQueued(**_subagent_common(raw))
    if kind == SubagentStarted.kind:
        return SubagentStarted(**_subagent_common(raw), workspace=str(raw.get("workspace", "")))
    if kind == SubagentProgress.kind:
        return SubagentProgress(
            **_subagent_common(raw),
            activity=str(raw.get("activity", "")),
            usage=_usage(raw.get("usage")),
        )
    if kind == SubagentBlocked.kind:
        return SubagentBlocked(**_subagent_common(raw), reason=str(raw.get("reason", "")))
    if kind == SubagentCompleted.kind:
        changed = raw.get("changed_files", ())
        verification = raw.get("verification", ())
        return SubagentCompleted(
            **_subagent_common(raw),
            commit=None if raw.get("commit") is None else str(raw.get("commit")),
            changed_files=tuple(
                str(item) for item in cast(list[object] | tuple[object, ...], changed)
            ),
            verification=tuple(
                str(item) for item in cast(list[object] | tuple[object, ...], verification)
            ),
            usage=_usage(raw.get("usage")),
        )
    if kind == SubagentFailed.kind:
        return SubagentFailed(
            **_subagent_common(raw),
            message=str(raw.get("message", "")),
            category=str(raw.get("category", "internal")),
            usage=_usage(raw.get("usage")),
        )
    if kind == SubagentCancelled.kind:
        return SubagentCancelled(
            **_subagent_common(raw),
            reason=str(raw.get("reason", "cancelled")),
            usage=_usage(raw.get("usage")),
        )
    if kind == SubagentIntegrated.kind:
        verification = raw.get("verification", ())
        return SubagentIntegrated(
            **_subagent_common(raw),
            commit=str(raw.get("commit", "")),
            verification=tuple(
                str(item) for item in cast(list[object] | tuple[object, ...], verification)
            ),
        )
    if kind == SubagentConflict.kind:
        conflict_files = raw.get("conflict_files", ())
        return SubagentConflict(
            **_subagent_common(raw),
            conflict_files=tuple(
                str(item) for item in cast(list[object] | tuple[object, ...], conflict_files)
            ),
            message=str(raw.get("message", "")),
        )
    if kind == SubagentCleanup.kind:
        return SubagentCleanup(
            **_subagent_common(raw),
            removed=bool(raw.get("removed", False)),
            retained_path=(
                None if raw.get("retained_path") is None else str(raw.get("retained_path"))
            ),
            reason=None if raw.get("reason") is None else str(raw.get("reason")),
        )
    raise ValueError(f"unknown agent event kind: {kind}")
