from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import OptionList, Static

from windcode import Windcode
from windcode.config import AppConfig
from windcode.domain.events import RunRequest
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta
from windcode.sessions import SessionMetadata, SessionStatus
from windcode.tui.app import WindcodeApp
from windcode.tui.widgets import ChatInput, SessionSelector


class SessionTransport:
    name = "session"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class SessionSelectorApp(App[None]):
    def __init__(self, sessions: tuple[SessionMetadata, ...]) -> None:
        super().__init__()
        self.sessions = sessions
        self.selected: list[str] = []
        self.cancelled = False

    def compose(self) -> ComposeResult:
        yield SessionSelector(self.sessions)

    def on_session_selector_selected(self, event: SessionSelector.Selected) -> None:
        self.selected.append(event.session_id)

    def on_session_selector_cancelled(self) -> None:
        self.cancelled = True


@pytest.mark.asyncio
async def test_session_selector_preselects_latest_without_emitting_selection() -> None:
    now = datetime.now(UTC)
    sessions = (
        SessionMetadata(
            session_id="older",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
            summary="较早任务",
            status=SessionStatus.COMPLETED,
        ),
        SessionMetadata(
            session_id="latest",
            created_at=now,
            updated_at=now,
            summary="最新任务",
            status=SessionStatus.COMPLETED,
        ),
    )
    app = SessionSelectorApp(sessions)

    async with app.run_test() as pilot:
        await pilot.pause()
        selector = app.query_one(SessionSelector)
        assert selector.value == "latest"
        label = str(selector.query_one("#label", Static).content)
        assert "最新任务" in label
        assert now.astimezone().strftime("%Y-%m-%d %H:%M") in label
        assert "latest" in label
        assert app.selected == []

        selector.focus()
        await pilot.press("enter")
        assert app.query_one(OptionList).option_count == 2
        await pilot.press("enter")
        assert app.selected == ["latest"]


@pytest.mark.asyncio
async def test_empty_session_selector_can_be_cancelled_with_escape() -> None:
    app = SessionSelectorApp(())

    async with app.run_test() as pilot:
        selector = app.query_one(SessionSelector)
        selector.focus()
        await pilot.press("escape")

        assert selector.has_focus
        assert app.cancelled


@pytest.mark.asyncio
async def test_resume_without_sessions_returns_to_chat_input_on_escape(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")

    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInput)
        chat_input.focus()
        await pilot.press("/", "r", "e", "s", "u", "m", "e", "enter")
        await pilot.pause()

        assert app.session_selector is not None
        assert app.session_selector.has_focus
        await pilot.press("escape")
        await pilot.pause()

        assert app.session_selector is None
        assert chat_input.has_focus


@pytest.mark.asyncio
async def test_sdk_lists_session_and_creates_rewind_branch(tmp_path: Path) -> None:
    state = tmp_path / "state"
    async with Windcode.open(state_root=state) as client:
        client.register_transport("session", "model", SessionTransport(), primary=True)
        await client.start_run(RunRequest("task", tmp_path, session_id="session")).result()
        sessions = client.list_sessions()
        assert [session.session_id for session in sessions] == ["session"]
        assert sessions[0].summary == "task"
        assert [message.role.value for message in client.load_session_messages("session")] == [
            "user",
            "assistant",
        ]

        from windcode.sessions import SessionStore

        store = SessionStore.open(state / "sessions", "session")
        source = store.load_records()[0]
        branch = client.rewind_session("session", source.record_id)
        assert branch.parent_id == source.record_id
