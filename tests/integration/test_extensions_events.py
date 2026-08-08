from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode import Windcode
from windcode.domain.events import ExtensionEvent, RunRequest
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta


class StopTransport:
    name = "stop"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_skill_event_is_persisted_before_run_subscriber_receives_it(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "extensions" / "complete_plugin"
    config = {"extensions": {"enabled": True}}
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=tmp_path) as client:
        client.register_transport("stop", "model", StopTransport(), primary=True)
        await client.install_extension(fixture, enable=True)
        await client.reload_extensions()

        handle = client.start_run(RunRequest("$review inspect", tmp_path, session_id="session"))
        extension_events: list[ExtensionEvent] = []
        async for event in handle:
            if not isinstance(event, ExtensionEvent):
                continue
            extension_events.append(event)
            persisted_ids = {
                str(record.payload.get("event_id"))
                for record in client.load_session_records("session")
                if record.record_type == "agent_event"
            }
            assert event.event_id in persisted_ids
        await handle.result()

    skill_event = next(event for event in extension_events if event.action == "skill_loaded")
    assert skill_event.snapshot_generation == 2
    assert skill_event.session_id == "session"
    assert skill_event.run_id
    assert skill_event.source_id.startswith("plugin:complete")
