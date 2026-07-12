import asyncio
import json
from pathlib import Path

import pytest

from windcode.domain.events import RunStarted, TextDeltaEvent
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


@pytest.mark.asyncio
async def test_transient_event_is_live_but_not_persisted_or_traced_by_default(
    tmp_path: Path,
) -> None:
    session = SessionStore.create(tmp_path / "sessions", "session")
    trace = TraceStore("run", root=tmp_path / "traces")
    bus = EventBus(session, trace)
    transient = TextDeltaEvent(
        event_id="delta",
        session_id="session",
        run_id="run",
        turn=1,
        text="partial",
    )

    stream = bus.subscribe()
    pending_transient = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    published = await bus.publish(transient)
    assert await pending_transient == transient
    durable = await bus.publish(event(1))

    assert await anext(stream) == durable
    assert published.sequence is None
    assert durable.sequence == 1
    assert [record.payload["event_id"] for record in session.load_records()] == ["event-1"]
    trace_records = [json.loads(line) for line in trace.path.read_text().splitlines()]
    assert [record["event"]["event_id"] for record in trace_records] == ["event-1"]
