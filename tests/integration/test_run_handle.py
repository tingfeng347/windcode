import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode import Windcode
from windcode.domain.events import RunCancelled, RunCompleted, RunRequest
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta


class TextTransport:
    name = "text"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class SlowTransport:
    name = "slow"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        await asyncio.sleep(60)
        yield TextDelta("late")

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_iterates_events_and_returns_idempotent_result(tmp_path: Path) -> None:
    async with Windcode.open(state_root=tmp_path / "state") as client:
        client.register_transport("test", "model", TextTransport(), primary=True)
        handle = client.start_run(RunRequest("task", tmp_path))
        events = [event async for event in handle]
        first = await handle.result()
        second = await handle.result()

    assert isinstance(events[-1], RunCompleted)
    assert first is second
    assert first.final_text == "done"


@pytest.mark.asyncio
async def test_resumed_run_does_not_replay_previous_run_events(tmp_path: Path) -> None:
    async with Windcode.open(state_root=tmp_path / "state") as client:
        client.register_transport("test", "model", TextTransport(), primary=True)

        first_handle = client.start_run(RunRequest("first", tmp_path, session_id="session"))
        first_events = [event async for event in first_handle]

        second_handle = client.start_run(RunRequest("second", tmp_path, session_id="session"))
        second_events = [event async for event in second_handle]

    assert first_events
    assert second_events
    assert {event.run_id for event in second_events} == {second_events[0].run_id}
    assert second_events[0].run_id != first_events[0].run_id
    assert sum(isinstance(event, RunCompleted) for event in second_events) == 1


@pytest.mark.asyncio
async def test_cancel_stops_model_task_and_records_terminal_event(tmp_path: Path) -> None:
    async with Windcode.open(state_root=tmp_path / "state") as client:
        client.register_transport("test", "model", SlowTransport(), primary=True)
        handle = client.start_run(RunRequest("task", tmp_path))
        await asyncio.sleep(0)
        await handle.cancel()
        events = [event async for event in handle]
        result = await handle.result()

    assert result.status == "cancelled"
    assert isinstance(events[-1], RunCancelled)
