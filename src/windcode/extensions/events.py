from __future__ import annotations

from typing import Any

from windcode.domain.events import ExtensionEvent

EXTENSION_ACTIONS = frozenset(
    {
        "discovery_completed",
        "diagnostic",
        "snapshot_reloaded",
        "capability_activated",
        "skill_loaded",
        "mcp_connecting",
        "mcp_connected",
        "mcp_called",
        "mcp_closed",
        "hook_started",
        "hook_finished",
        "hook_rejected",
        "plugin_state_changed",
    }
)


def extension_event(
    *,
    event_id: str,
    session_id: str,
    run_id: str,
    turn: int,
    action: str,
    snapshot_generation: int,
    extension_id: str,
    source_id: str,
    status: str = "",
    server_id: str | None = None,
    hook_id: str | None = None,
    call_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> ExtensionEvent:
    if action not in EXTENSION_ACTIONS:
        raise ValueError(f"unknown extension event action: {action}")
    return ExtensionEvent(
        event_id=event_id,
        session_id=session_id,
        run_id=run_id,
        turn=turn,
        action=action,
        snapshot_generation=snapshot_generation,
        extension_id=extension_id,
        source_id=source_id,
        status=status,
        server_id=server_id,
        hook_id=hook_id,
        call_id=call_id,
        details={} if details is None else dict(details),
    )
