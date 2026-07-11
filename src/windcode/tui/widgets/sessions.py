from __future__ import annotations

from textual.message import Message
from textual.widgets import Select

from windcode.sessions import SessionMetadata


class SessionSelector(Select[str]):
    class Selected(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(self, sessions: tuple[SessionMetadata, ...]) -> None:
        statuses = {
            "running": "运行中",
            "completed": "已完成",
            "unverified": "未验证",
            "failed": "失败",
            "cancelled": "已取消",
            "interrupted": "已中断",
        }
        options = [
            (
                f"{session.session_id[:12]}  "
                f"{statuses.get(session.status.value, session.status.value)}",
                session.session_id,
            )
            for session in sessions
        ]
        super().__init__(options, prompt="选择会话", allow_blank=True, id="sessions")

    def on_select_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.post_message(self.Selected(event.value))
