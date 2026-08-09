from __future__ import annotations

from pathlib import Path

from windcode.domain.messages import (
    Message,
    Role,
    TextBlock,
    heal_dangling_tool_calls,
    message_from_dict,
)
from windcode.sessions import (
    EventRecord,
    SessionMetadata,
    SessionStore,
    ancestor_chain,
    create_branch,
)


class SessionApplication:
    """Own session lookup, history reconstruction, summaries, and rewinds."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root

    @property
    def sessions_root(self) -> Path:
        return self.state_root / "sessions"

    def _store(self, session_id: str) -> SessionStore:
        return SessionStore.open(self.sessions_root, session_id)

    def exists(self, session_id: str) -> bool:
        return (self.sessions_root / session_id / "meta.json").is_file()

    def load_records(self, session_id: str) -> tuple[EventRecord, ...]:
        store = self._store(session_id)
        if store.metadata.head_record_id is None:
            return ()
        return ancestor_chain(store.load_records(), store.metadata.head_record_id)

    def load_messages(self, session_id: str) -> tuple[Message, ...]:
        return heal_dangling_tool_calls(
            tuple(
                message_from_dict(record.payload)
                for record in self.load_records(session_id)
                if record.record_type == "conversation_message"
            )
        )

    @staticmethod
    def _summary(prompt: str, *, limit: int = 60) -> str:
        summary = " ".join(prompt.split())
        if len(summary) <= limit:
            return summary
        return summary[: limit - 3].rstrip() + "..."

    def _ensure_summary(self, store: SessionStore) -> SessionMetadata:
        if store.metadata.summary:
            return store.metadata
        for message in self.load_messages(store.metadata.session_id):
            if message.role is not Role.USER:
                continue
            text = "".join(
                block.text for block in message.content if isinstance(block, TextBlock)
            ).strip()
            if text:
                store.set_summary(self._summary(text))
                break
        return store.metadata

    def list(self) -> tuple[SessionMetadata, ...]:
        if not self.sessions_root.exists():
            return ()
        sessions: list[SessionMetadata] = []
        for path in self.sessions_root.iterdir():
            if not path.is_dir() or not (path / "meta.json").is_file():
                continue
            sessions.append(self._ensure_summary(SessionStore.open(self.sessions_root, path.name)))
        return tuple(sorted(sessions, key=lambda item: item.updated_at, reverse=True))

    def rewind(
        self,
        session_id: str,
        record_id: str,
        *,
        include_selected: bool = False,
    ) -> EventRecord:
        store = self._store(session_id)
        parent_id = record_id
        if include_selected:
            records = {record.record_id: record for record in store.load_records()}
            try:
                parent_id = records[record_id].parent_id
            except KeyError as exc:
                raise ValueError(f"unknown session record id: {record_id}") from exc
            if parent_id is None:
                return store.append(
                    "branch_point",
                    {"source_record_id": record_id},
                    root=True,
                    durable=True,
                )
        return create_branch(
            store,
            parent_id,
            "branch_point",
            {"source_record_id": record_id},
        )
