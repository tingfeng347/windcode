from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from textual.widgets import Static

from windcode.config import AppConfig
from windcode.domain.messages import TextBlock
from windcode.domain.models import ModelCompleted, ModelEvent, ModelRequest, StopReason, TextDelta
from windcode.tui import WindcodeApp
from windcode.tui.widgets import ChatInput, CommandMenu, MessageStream, WelcomeView


class EchoTransport:
    name = "echo"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        prompt = next(
            block.text
            for message in reversed(request.messages)
            for block in message.content
            if isinstance(block, TextBlock)
        )
        yield TextDelta(f"回复:{prompt}")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_new_session_shows_welcome_and_accepts_status_command(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.query_one("#chat-input", ChatInput).has_focus
        assert app.ui_mode == "welcome"
        assert app.query_one("#welcome-view", WelcomeView).display
        assert not app.query_one("#chat-area", MessageStream).display

        await pilot.press("/", "s", "t", "a", "t", "u", "s", "enter")
        await pilot.pause()

        notice = app.query_one("#welcome-notice", Static)
        assert "会话: 新会话" in str(notice.content)
        assert not app.query_one("#command-menu", CommandMenu).is_open


@pytest.mark.asyncio
async def test_resumed_session_uses_compact_chat_layout(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(), workspace=tmp_path, state_root=tmp_path / "state", session_id="existing"
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-area", MessageStream)
        prompt = app.query_one("#chat-input", ChatInput)
        status = app.query_one("#status-bar")

        assert chat.region.width >= 116
        assert chat.region.bottom <= prompt.region.y
        assert prompt.region.bottom <= status.region.y
        assert app.query_one("#title-bar").region.height == 1
        assert not app.query_one("#welcome-view", WelcomeView).display
        assert app.ui_mode == "chat"
        assert app.query_one("#mode-label", Static)
        assert app.query_one("#model-label", Static)


@pytest.mark.asyncio
async def test_narrow_layout_keeps_input_and_status_separate(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(40, 24)) as pilot:
        await pilot.pause()
        prompt = app.query_one("#chat-input", ChatInput)
        status = app.query_one("#status-bar")

        assert prompt.region.bottom <= status.region.y
        assert app.has_class("narrow")
        assert prompt.region.width == 39


@pytest.mark.asyncio
async def test_new_command_returns_to_welcome_mode(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(), workspace=tmp_path, state_root=tmp_path / "state", session_id="existing"
    )
    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.focus()
        await pilot.press("/", "n", "e", "w", "enter")
        await pilot.pause()

        assert app.ui_mode == "welcome"
        assert app.session_id is None
        assert app.query_one("#welcome-view", WelcomeView).display
        assert prompt.has_focus


@pytest.mark.asyncio
async def test_slash_menu_is_above_prompt_in_real_app(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)
        await pilot.press("/")
        await pilot.pause()

        menu = app.query_one("#command-menu", CommandMenu)
        status = app.query_one("#status-bar")
        assert menu.is_open
        assert menu.region.bottom <= prompt.region.y
        assert prompt.region.bottom <= status.region.y
        assert menu.region.width == prompt.region.width


@pytest.mark.asyncio
async def test_resumed_tui_turn_does_not_render_previous_reply(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("echo", "model", EchoTransport(), primary=True)
        prompt = app.query_one("#chat-input", ChatInput)

        prompt.insert("第一轮")
        await pilot.press("enter")
        assert app.ui_mode == "chat"
        assert not app.query_one("#welcome-view", WelcomeView).display
        while app.handle is None or not app.handle.done:
            await pilot.pause(0.01)
        await pilot.pause()

        prompt.insert("第二轮")
        await pilot.press("enter")
        while not app.handle.done:
            await pilot.pause(0.01)
        await pilot.pause()

        replies = [message for message in app.query(".ai-message") if isinstance(message, Static)]
        assert len(replies) == 2
        assert "回复:第一轮" in str(replies[0].content)
        assert "回复:第二轮" in str(replies[1].content)
        assert "回复:第一轮" not in str(replies[1].content)
