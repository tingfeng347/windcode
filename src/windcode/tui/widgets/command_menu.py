from __future__ import annotations

from rich.markup import escape
from textual.widgets import Static

from windcode.tui.commands import CommandDefinition


class CommandMenu(Static):
    """Keyboard-driven slash command candidates shown above the prompt."""

    visible_items = 8

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__("", name=name, id=id, classes=classes, disabled=disabled)
        self._items: tuple[CommandDefinition, ...] = ()
        self._cursor = 0
        self.display = False

    @property
    def is_open(self) -> bool:
        return bool(self.display and self._items)

    @property
    def items(self) -> tuple[CommandDefinition, ...]:
        return self._items

    @property
    def cursor(self) -> int:
        return self._cursor

    def show_commands(self, items: tuple[CommandDefinition, ...]) -> None:
        if not items:
            self.hide()
            return
        self._items = items
        self._cursor = 0
        self.display = True
        self._render_items()

    def hide(self) -> None:
        self.display = False
        self._items = ()
        self._cursor = 0
        self.update("")

    def move_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._render_items()

    def move_down(self) -> None:
        if self._cursor < len(self._items) - 1:
            self._cursor += 1
            self._render_items()

    def selected_value(self) -> str | None:
        if not self._items:
            return None
        return self._items[self._cursor].value

    def _render_items(self) -> None:
        window_start = min(
            max(0, self._cursor - self.visible_items + 1),
            max(0, len(self._items) - self.visible_items),
        )
        visible = self._items[window_start : window_start + self.visible_items]
        command_width = max(
            len(item.value) + (len(item.argument_hint) + 1 if item.argument_hint else 0)
            for item in visible
        )
        lines: list[str] = []
        for offset, item in enumerate(visible):
            index = window_start + offset
            command = item.value
            if item.argument_hint:
                command = f"{command} {item.argument_hint}"
            label = f"{command:<{command_width}}  {item.description}"
            if index == self._cursor:
                lines.append(f"[bold reverse]> {escape(label)} [/]")
            else:
                lines.append(f"  [dim]{escape(label)}[/]")
        self.update("\n".join(lines))
