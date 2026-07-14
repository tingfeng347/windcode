from __future__ import annotations

from typing import Any, ClassVar, cast

from textual import on
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Select
from textual.widgets._select import SelectOverlay

from windcode.sessions import EventRecord, SessionMetadata


def _history_label(record: EventRecord) -> str:
    raw_content = record.payload.get("content", [])
    text_blocks: list[str] = []
    if isinstance(raw_content, list):
        for raw_block in cast(list[object], raw_content):
            if not isinstance(raw_block, dict):
                continue
            block = cast(dict[str, Any], raw_block)
            if block.get("type") == "text":
                text_blocks.append(str(block.get("text", "")))
    text = " ".join(text_blocks)
    preview = " ".join(text.split())[:48]
    return preview or "[无文本内容]"


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


class RewindSelector(Select[str]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "返回", priority=True)]

    class Selected(Message):
        def __init__(self, record_id: str) -> None:
            super().__init__()
            self.record_id = record_id

    class Cancelled(Message):
        pass

    def __init__(self, records: tuple[EventRecord, ...]) -> None:
        # User messages are the useful rewind points. Present them newest-first.
        options = [
            (
                f"{_history_label(record)}  ·  "
                f"{record.created_at.astimezone().strftime('%Y-%m-%d %H:%M')}  ·  "
                f"{record.record_id[:12]}",
                record.record_id,
            )
            for record in reversed(records)
            if record.record_type == "conversation_message" and record.payload.get("role") == "user"
        ]
        super().__init__(
            options,
            prompt="暂无可回退的历史记录",
            allow_blank=not options,
            value=options[0][1] if options else Select.NULL,
            id="rewind-history",
        )

    def action_cancel(self) -> None:
        self.expanded = False
        self.post_message(self.Cancelled())

    @on(SelectOverlay.UpdateSelection)
    def _update_selection(self, event: SelectOverlay.UpdateSelection) -> None:
        """Confirm a history node even when it is already highlighted."""
        event.stop()
        value = self._options[event.option_index][1]
        if isinstance(value, str):
            if value != self.value:
                self.value = value
            self.post_message(self.Selected(value))
        self.focus()
        self.expanded = False
