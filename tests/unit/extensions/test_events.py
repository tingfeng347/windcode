import pytest

from windcode.domain.events import event_from_dict, event_to_dict
from windcode.extensions.events import extension_event


def test_extension_event_round_trip_preserves_correlation() -> None:
    event = extension_event(
        event_id="event",
        session_id="session",
        run_id="run",
        turn=1,
        action="mcp_called",
        snapshot_generation=3,
        extension_id="plugin:x",
        source_id="plugin:x/server",
        server_id="server",
        call_id="call",
    )
    assert event_from_dict(event_to_dict(event)) == event


def test_extension_event_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="unknown extension event action"):
        extension_event(
            event_id="e",
            session_id="s",
            run_id="r",
            turn=0,
            action="arbitrary",
            snapshot_generation=0,
            extension_id="x",
            source_id="x",
        )
