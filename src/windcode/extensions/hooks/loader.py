from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

from windcode.domain.tools import ToolEffect
from windcode.extensions.hooks.models import (
    DECISION_EVENTS,
    CommandAction,
    HookDefinition,
    HookEvent,
    HookMatcher,
    NotifyAction,
    PromptAction,
    RejectAction,
    TightenAction,
)
from windcode.extensions.models import normalize_id
from windcode.extensions.paths import read_bounded


def load_hook_definition(
    root: Path, relative_path: str, *, source_id: str, max_bytes: int = 65_536
) -> HookDefinition:
    try:
        raw = cast(
            dict[str, object],
            tomllib.loads(read_bounded(root, relative_path, max_bytes=max_bytes).decode("utf-8")),
        )
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid Hook definition: {exc}") from exc
    allowed = {
        "id",
        "event",
        "tool_id",
        "status",
        "priority",
        "timeout_seconds",
        "output_limit",
        "required",
        "action",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown Hook fields: {', '.join(sorted(unknown))}")
    hook_id = normalize_id(str(raw.get("id", "")))
    event = HookEvent(str(raw.get("event", "")))
    action_raw = raw.get("action")
    if not isinstance(action_raw, dict):
        raise ValueError("Hook action must be a table")
    action_data = cast(dict[str, object], action_raw)
    action_type = str(action_data.get("type", ""))
    if action_type == "notify" and set(action_data) == {"type", "message"}:
        action = NotifyAction(str(action_data["message"]))
    elif action_type == "prompt" and set(action_data) == {"type", "content"}:
        action = PromptAction(str(action_data["content"]))
    elif action_type == "command" and set(action_data) == {"type", "command"}:
        action = CommandAction(str(action_data["command"]))
    elif action_type == "reject" and set(action_data) == {"type", "reason"}:
        action = RejectAction(str(action_data["reason"]))
    elif action_type == "tighten" and set(action_data) == {"type", "effects"}:
        effects = action_data["effects"]
        if not isinstance(effects, list):
            raise ValueError("tighten effects must be an array")
        effect_values = cast(list[object], effects)
        action = TightenAction(frozenset(ToolEffect(str(value)) for value in effect_values))
    else:
        raise ValueError(f"invalid Hook action: {action_type}")
    if isinstance(action, (RejectAction, TightenAction)) and event not in DECISION_EVENTS:
        raise ValueError("reject and tighten actions are only valid before tool policy")
    return HookDefinition(
        hook_id,
        source_id,
        HookMatcher(
            event,
            None if raw.get("tool_id") is None else str(raw["tool_id"]),
            None if raw.get("status") is None else str(raw["status"]),
        ),
        action,
        _int_field(raw.get("priority"), 100, "priority"),
        _float_field(raw.get("timeout_seconds"), 10.0, "timeout_seconds"),
        _int_field(raw.get("output_limit"), 4096, "output_limit"),
        bool(raw.get("required", False)),
    )


def _int_field(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Hook {name} must be an integer")
    return value


def _float_field(value: object, default: float, name: str) -> float:
    if value is None:
        return default
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Hook {name} must be a number")
    return float(value)
