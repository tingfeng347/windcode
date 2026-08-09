from __future__ import annotations

from windcode.domain.events import MemoryEvent, event_from_dict, event_to_dict


def test_memory_event_round_trip() -> None:
    event = MemoryEvent(
        event_id="event",
        session_id="session",
        run_id="run",
        turn=1,
        action="candidate_created",
        memory_id="memory",
        memory_kind="experience",
        scope="project",
        status="candidate",
        details={"source": "run"},
    )
    assert event_from_dict(event_to_dict(event)) == event
