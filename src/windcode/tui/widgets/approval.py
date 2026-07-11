from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from windcode.domain.events import ApprovalRequested

CHOICE_LABELS = {
    "allow_once": "仅本次允许",
    "allow_session": "会话内允许",
    "deny": "拒绝",
}


class ApprovalWidget(Vertical, can_focus=True):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "向上", priority=True),
        Binding("down", "cursor_down", "向下", priority=True),
        Binding("enter", "select", "选择", priority=True),
        Binding("escape", "deny", "拒绝", priority=True),
    ]

    class Decision(Message):
        def __init__(self, request_id: str, decision: str) -> None:
            super().__init__()
            self.request_id = request_id
            self.decision = decision

    def __init__(self, request: ApprovalRequested) -> None:
        super().__init__(classes="interaction", id=f"approval-{request.request_id}")
        self.request = request
        self.cursor = 0

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="approval-content")

    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> str:
        risk = {"low": "低风险", "medium": "中风险", "high": "高风险"}.get(
            self.request.risk, self.request.risk
        )
        lines = [
            f"\n  [bold yellow]需要授权 · {risk}[/bold yellow]\n",
            f"    {self.request.summary}\n",
            "  [dim]是否继续执行?[/dim]\n",
        ]
        for index, choice in enumerate(self.request.choices):
            label = CHOICE_LABELS.get(choice, choice)
            if index == self.cursor:
                lines.append(f" [bold cyan]>[/bold cyan] {index + 1}. [bold]{label}[/bold]")
            else:
                lines.append(f"   {index + 1}. [dim]{label}[/dim]")
        return "\n".join(lines)

    def _refresh(self) -> None:
        self.query_one("#approval-content", Static).update(self._build_content())

    def action_cursor_up(self) -> None:
        if self.cursor > 0:
            self.cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self.cursor < len(self.request.choices) - 1:
            self.cursor += 1
            self._refresh()

    def action_select(self) -> None:
        decision = self.request.choices[self.cursor]
        self.post_message(self.Decision(self.request.request_id, decision))

    def action_deny(self) -> None:
        self.post_message(self.Decision(self.request.request_id, "deny"))
