from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

from windcode.domain.events import AgentEventType, event_from_dict, event_to_dict
from windcode.observability import TraceStore
from windcode.sessions import SessionStore

_TRANSIENT_EVENT_KINDS = frozenset(
    {"text_delta", "reasoning_status", "tool_progress", "subagent_progress"}
)


class EventBus:
    def __init__(self, session_store: SessionStore, trace_store: TraceStore) -> None:
        self.session_store = session_store
        self.trace_store = trace_store
        self._queue: asyncio.Queue[AgentEventType | None] = asyncio.Queue()
        self._closed = False

    async def publish(self, event: AgentEventType, *, durable: bool = False) -> AgentEventType:
        if self._closed:
            raise RuntimeError("event bus is closed")
        if event.kind in _TRANSIENT_EVENT_KINDS:
            self.trace_store.write(event, durable=durable)
            await self._queue.put(event)
            return event
        record = self.session_store.append("agent_event", event_to_dict(event), durable=durable)
        persisted = replace(event, sequence=record.sequence)
        self.trace_store.write(persisted, durable=durable)
        await self._queue.put(persisted)
        return persisted

    def _replay(self, after_sequence: int) -> tuple[AgentEventType, ...]:
        replayed: list[AgentEventType] = []
        for record in self.session_store.load_records():
            if record.record_type != "agent_event" or record.sequence <= after_sequence:
                continue
            event = event_from_dict(record.payload)
            replayed.append(replace(event, sequence=record.sequence))
        return tuple(replayed)

    async def subscribe(self, *, after_sequence: int = 0) -> AsyncIterator[AgentEventType]:
        replayed = () if not self._queue.empty() else self._replay(after_sequence)
        highest = after_sequence
        for event in replayed:
            highest = max(highest, event.sequence or 0)
            yield event
        while True:
            event = await self._queue.get()
            if event is None:
                return
            if event.sequence is None:
                yield event
                continue
            if event.sequence <= highest:
                continue
            highest = event.sequence
            yield event

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)
