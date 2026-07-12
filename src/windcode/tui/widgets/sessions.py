from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Select
from textual.widgets._select import SelectOverlay

from windcode.sessions import SessionMetadata


class SessionSelector(Select[str]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "返回", priority=True)]

    class Selected(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    class Cancelled(Message):
        pass

    def __init__(self, sessions: tuple[SessionMetadata, ...]) -> None:
        statuses = {
            "active": "进行中",
            "running": "运行中",
            "completed": "已完成",
            "unverified": "已完成 · 未验证",
            "failed": "失败",
            "cancelled": "已取消",
            "interrupted": "已中断",
        }
        ordered_sessions = sorted(sessions, key=lambda session: session.updated_at, reverse=True)
        options = [
            (
                f"{session.summary or '未命名会话'}  ·  "
                f"{session.updated_at.astimezone().strftime('%Y-%m-%d %H:%M')}  ·  "
                f"{statuses.get(session.status.value, session.status.value)}  ·  "
                f"{session.session_id[:12]}",
                session.session_id,
            )
            for session in ordered_sessions
        ]
        super().__init__(
            options,
            prompt="暂无可恢复的会话",
            allow_blank=not options,
            value=options[0][1] if options else Select.NULL,
            id="sessions",
        )

    def action_cancel(self) -> None:
        self.expanded = False
        self.post_message(self.Cancelled())

    @on(SelectOverlay.UpdateSelection)
    def _update_selection(self, event: SelectOverlay.UpdateSelection) -> None:
        """Confirm a session even when the highlighted option is already selected."""
        event.stop()
        value = self._options[event.option_index][1]
        if isinstance(value, str):
            if value != self.value:
                self.value = value
            self.post_message(self.Selected(value))
        self.focus()
        self.expanded = False
