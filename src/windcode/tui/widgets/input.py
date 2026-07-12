from __future__ import annotations

from typing import Any, ClassVar

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from windcode.tui.widgets.command_menu import CommandMenu


class ChatInput(TextArea):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "submit", "发送", priority=True),
        Binding("shift+enter", "newline", "换行", priority=True),
        Binding("ctrl+j", "newline", "换行", priority=True),
        Binding("tab", "complete", "补全", priority=True),
        Binding("escape", "dismiss_menu", "关闭菜单", priority=True),
        Binding("up", "nav_up", "向上选择", priority=True),
        Binding("down", "nav_down", "向下选择", priority=True),
    ]

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class SlashMenuUpdate(Message):
        def __init__(self, prefix: str | None) -> None:
            super().__init__()
            self.prefix = prefix

    class SkillMenuUpdate(Message):
        def __init__(self, prefix: str | None) -> None:
            super().__init__()
            self.prefix = prefix

    class EscapePressed(Message):
        pass

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cursor_blink = False

    def action_submit(self) -> None:
        menu = self._command_menu()
        if menu is not None and menu.is_open:
            selected = menu.selected_value()
            menu.hide()
            if selected is not None:
                self.post_message(self.Submitted(selected))
                self.clear()
                return
        text = self.text.strip()
        if text:
            self.post_message(self.Submitted(text))
            self.clear()

    def action_newline(self) -> None:
        self.insert("\n")

    def action_complete(self) -> None:
        menu = self._command_menu()
        if menu is not None and menu.is_open:
            selected = menu.selected_value()
            if selected is not None:
                menu.hide()
                self.clear()
                self.insert(f"{selected} ")
            return
        self.insert("\t")

    def action_dismiss_menu(self) -> None:
        menu = self._command_menu()
        if menu is not None and menu.is_open:
            menu.hide()
        else:
            self.post_message(self.EscapePressed())
        self.focus()

    def action_nav_up(self) -> None:
        menu = self._command_menu()
        if menu is not None and menu.is_open:
            menu.move_up()
            return
        self.action_cursor_up()

    def action_nav_down(self) -> None:
        menu = self._command_menu()
        if menu is not None and menu.is_open:
            menu.move_down()
            return
        self.action_cursor_down()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        event.stop()
        value = self.text
        single_token = " " not in value and "\n" not in value
        if value.startswith("/") and single_token:
            self.post_message(self.SlashMenuUpdate(value))
        elif value.startswith("$") and single_token:
            self.post_message(self.SkillMenuUpdate(value))
        else:
            self.post_message(self.SlashMenuUpdate(None))

    def _command_menu(self) -> CommandMenu | None:
        try:
            return self.screen.query_one(CommandMenu)
        except Exception:
            return None
