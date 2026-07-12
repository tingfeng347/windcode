from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from rich.text import Text as RichText
from textual.widgets import Static

from windcode import Windcode
from windcode.config import AppConfig, SandboxConfig
from windcode.domain.events import RunRequest
from windcode.domain.messages import TextBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
)
from windcode.tui import WindcodeApp
from windcode.tui.widgets import ApprovalWidget, ChatInput, CommandMenu, MessageStream, WelcomeView


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


class ShellTransport:
    name = "shell"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if request.messages[-1].role.value == "user":
            yield ToolCallDelta("shell", "shell", '{"command":"printf ok"}')
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("完成")
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
        assert "委派: explicit" in str(notice.content)
        assert not app.query_one("#command-menu", CommandMenu).is_open


@pytest.mark.asyncio
async def test_shift_tab_cycles_permission_modes_and_updates_ui(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(80, 24)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)

        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.permission_mode == "accept_edits"
        assert "自动编辑" in str(app.query_one("#mode-label", Static).content)

        await pilot.press("backtab", "backtab", "backtab")
        await pilot.pause()
        assert app.permission_mode == "default"
        assert prompt.has_focus


@pytest.mark.asyncio
async def test_welcome_logo_animates_with_multiple_colors(tmp_path: Path) -> None:
    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=tmp_path / "state")
    async with app.run_test(size=(100, 30)) as pilot:
        logo = app.query_one("#welcome-logo", Static)
        first = logo.render()
        first_styles = tuple(str(span.style) for span in first.spans)
        await pilot.pause(0.15)
        second = logo.render()

        assert first_styles != tuple(str(span.style) for span in second.spans)
        colors = {str(span.style) for span in second.spans}
        assert len(colors) >= 4


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
        assert "委派: 显式" in str(app.query_one("#sandbox-label", Static).content)


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


@pytest.mark.asyncio
async def test_opening_existing_session_replays_visible_conversation(tmp_path: Path) -> None:
    state = tmp_path / "state"
    async with Windcode.open(state_root=state) as client:
        client.register_transport("echo", "model", EchoTransport(), primary=True)
        await client.start_run(RunRequest("历史问题", tmp_path, session_id="session")).result()

    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=state, session_id="session")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        users = [message for message in app.query(".user-message") if isinstance(message, Static)]
        replies = [message for message in app.query(".ai-message") if isinstance(message, Static)]
        assert len(users) == 1
        assert len(replies) == 1
        assert "历史问题" in str(users[0].content)
        assert "回复:历史问题" in str(replies[0].content)


@pytest.mark.asyncio
async def test_idle_compact_and_history_commands_are_usable(tmp_path: Path) -> None:
    state = tmp_path / "state"
    async with Windcode.open(state_root=state) as client:
        client.register_transport("echo", "model", EchoTransport(), primary=True)
        await client.start_run(RunRequest("历史问题", tmp_path, session_id="session")).result()

    app = WindcodeApp(AppConfig(), workspace=tmp_path, state_root=state, session_id="session")
    async with app.run_test(size=(100, 30)) as pilot:
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.focus()
        prompt.insert("/compact")
        await pilot.press("enter")
        await pilot.pause()
        assert app.compact_next_run

        prompt.insert("/history")
        await pilot.press("enter")
        await pilot.pause()
        system_messages = [
            message for message in app.query(".system-message") if isinstance(message, Static)
        ]
        assert any("当前会话历史节点" in str(message.content) for message in system_messages)


@pytest.mark.asyncio
async def test_input_regains_focus_after_approved_agent_run(tmp_path: Path) -> None:
    app = WindcodeApp(
        AppConfig(sandbox=SandboxConfig(enabled=False)),
        workspace=tmp_path,
        state_root=tmp_path / "state",
    )
    async with app.run_test(size=(80, 24)) as pilot:
        app.client.register_transport("shell", "model", ShellTransport(), primary=True)
        prompt = app.query_one("#chat-input", ChatInput)
        prompt.insert("执行命令")
        await pilot.press("enter")

        for _ in range(200):
            if list(app.query(ApprovalWidget)):
                break
            await pilot.pause(0.01)
        else:
            pytest.fail("approval widget was not shown")

        approval = app.query_one(ApprovalWidget)
        approval_content = str(approval.query_one("#approval-content", Static).content)
        assert "bash: printf ok" in RichText.from_markup(approval_content).plain
        await pilot.press("down", "enter")
        while app.handle is None or not app.handle.done:
            await pilot.pause(0.01)
        await pilot.pause()

        assert prompt.has_focus
