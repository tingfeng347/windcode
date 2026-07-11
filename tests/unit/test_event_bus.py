import json
from pathlib import Path

import pytest

from windcode.domain.events import RunStarted
from windcode.observability import TraceStore
from windcode.runtime.event_bus import EventBus
from windcode.sessions import SessionStore


def event(number: int) -> RunStarted:
    return RunStarted(
        event_id=f"event-{number}",
        session_id="session",
        run_id="run",
        turn=number,
        prompt=f"prompt-{number}",
    )


@pytest.mark.asyncio
async def test_persists_to_both_stores_before_event_is_visible(tmp_path: Path) -> None:
    session = SessionStore.create(tmp_path / "sessions", "session")
    trace = TraceStore("run", root=tmp_path / "traces")
    bus = EventBus(session, trace)
    published = await bus.publish(event(1), durable=True)

    stream = bus.subscribe()
    visible = await anext(stream)

    assert visible == published
    assert session.load_records()[0].payload["event_id"] == "event-1"
    trace_record = json.loads(trace.path.read_text())
    assert trace_record["event"]["event_id"] == "event-1"


@pytest.mark.asyncio
async def test_replays_after_sequence_then_continues_live_without_duplicates(
    tmp_path: Path,
) -> None:
    session = SessionStore.create(tmp_path / "sessions", "session")
    trace = TraceStore("run", root=tmp_path / "traces")
    first_bus = EventBus(session, trace)
    await first_bus.publish(event(1))
    await first_bus.publish(event(2))

    resumed = EventBus(session, trace)
    stream = resumed.subscribe(after_sequence=1)
    assert (await anext(stream)).event_id == "event-2"
    await resumed.publish(event(3))
    assert (await anext(stream)).event_id == "event-3"
    await resumed.close()
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
