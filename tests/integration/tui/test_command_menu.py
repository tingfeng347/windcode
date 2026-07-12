from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from windcode.tui.commands import COMMAND_CATALOG, complete_commands
from windcode.tui.widgets import ChatInput, CommandMenu


class CommandApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[str] = []

    def compose(self) -> ComposeResult:
        yield CommandMenu(id="command-menu")
        yield ChatInput(id="chat-input")

    def on_chat_input_slash_menu_update(self, event: ChatInput.SlashMenuUpdate) -> None:
        menu = self.query_one(CommandMenu)
        matches = complete_commands(event.prefix) if event.prefix is not None else ()
        menu.show_commands(matches) if matches else menu.hide()

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        self.submitted.append(event.text)


@pytest.mark.asyncio
async def test_menu_tracks_candidates_and_navigation(tmp_path: Path) -> None:
    del tmp_path
    app = CommandApp()
    async with app.run_test() as pilot:
        menu = app.query_one(CommandMenu)
        menu.show_commands(COMMAND_CATALOG[:2])

        assert menu.is_open
        assert menu.selected_value() == "/new"
        menu.move_up()
        assert menu.cursor == 0
        menu.move_down()
        menu.move_down()
        assert menu.cursor == 1
        assert menu.selected_value() == "/resume"

        menu.hide()
        await pilot.pause()
        assert not menu.is_open
        assert menu.items == ()


@pytest.mark.asyncio
async def test_slash_filters_and_tab_completes_selected_command(tmp_path: Path) -> None:
    del tmp_path
    app = CommandApp()
    async with app.run_test() as pilot:
        prompt = app.query_one(ChatInput)
        prompt.focus()
        await pilot.press("/", "m", "o")
        await pilot.pause()

        menu = app.query_one(CommandMenu)
        assert [item.name for item in menu.items] == ["mode", "model"]
        await pilot.press("down", "tab")
        await pilot.pause()

        assert prompt.text == "/model "
        assert prompt.has_focus
        assert not menu.is_open


@pytest.mark.asyncio
async def test_navigation_keeps_highlight_in_visible_window(tmp_path: Path) -> None:
    del tmp_path
    app = CommandApp()
    async with app.run_test() as pilot:
        menu = app.query_one(CommandMenu)
        menu.show_commands(COMMAND_CATALOG)
        for _ in range(8):
            menu.move_down()
        await pilot.pause()

        assert menu.cursor == 8
        assert f"> {COMMAND_CATALOG[8].value}" in str(menu.render())
        assert COMMAND_CATALOG[0].value not in str(menu.render())


@pytest.mark.asyncio
async def test_enter_submits_selected_command_and_escape_closes_menu(tmp_path: Path) -> None:
    del tmp_path
    app = CommandApp()
    async with app.run_test() as pilot:
        prompt = app.query_one(ChatInput)
        prompt.focus()
        await pilot.press("/", "s")
        await pilot.pause()
        assert app.query_one(CommandMenu).is_open

        await pilot.press("enter")
        await pilot.pause()
        assert app.submitted == ["/status"]
        assert prompt.text == ""

        await pilot.press("/", "m", "escape")
        await pilot.pause()
        assert not app.query_one(CommandMenu).is_open
        assert prompt.text == "/m"
        assert prompt.has_focus


@pytest.mark.asyncio
async def test_arrow_keys_keep_native_cursor_movement_when_menu_is_closed(tmp_path: Path) -> None:
    del tmp_path
    app = CommandApp()
    async with app.run_test() as pilot:
        prompt = app.query_one(ChatInput)
        prompt.focus()
        prompt.insert("第一行\n第二行")
        await pilot.press("up")
        await pilot.pause()

        assert prompt.cursor_location[0] == 0
