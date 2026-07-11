from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode import Windcode
from windcode.domain.events import RunRequest
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta


class SessionTransport:
    name = "session"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_sdk_lists_session_and_creates_rewind_branch(tmp_path: Path) -> None:
    state = tmp_path / "state"
    async with Windcode.open(state_root=state) as client:
        client.register_transport("session", "model", SessionTransport(), primary=True)
        await client.start_run(RunRequest("task", tmp_path, session_id="session")).result()
        sessions = client.list_sessions()
        assert [session.session_id for session in sessions] == ["session"]

        from windcode.sessions import SessionStore

        store = SessionStore.open(state / "sessions", "session")
        source = store.load_records()[0]
        branch = client.rewind_session("session", source.record_id)
        assert branch.parent_id == source.record_id
