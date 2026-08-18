from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import cast
from uuid import uuid4

from windcode import Windcode
from windcode.config import load_config
from windcode.domain.events import AgentEventType, RunRequest, event_to_dict
from windcode.providers import ProviderService
from windcode.runtime.run_handle import RunHandle


def json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): json_value(item) for key, item in mapping.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        sequence = cast(tuple[object, ...] | list[object] | set[object] | frozenset[object], value)
        return [json_value(item) for item in sequence]
    return value


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    workspace_id: str
    name: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {"id": self.workspace_id, "name": self.name, "path": str(self.path)}


class WorkspaceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, WorkspaceEntry] = {}
        self.selected: str | None = None
        self._load()

    @staticmethod
    def identity(path: Path) -> str:
        return hashlib.sha256(str(path).encode()).hexdigest()[:16]

    def _load(self) -> None:
        try:
            parsed = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(parsed, dict):
            return
        raw = cast(dict[str, object], parsed)
        workspaces = raw.get("workspaces")
        if not isinstance(workspaces, list):
            workspaces = []
        for item in cast(list[object], workspaces):
            if not isinstance(item, dict):
                continue
            value = cast(dict[str, object], item)
            try:
                path = Path(str(value["path"])).expanduser().resolve()
            except (KeyError, OSError):
                continue
            entry = WorkspaceEntry(
                str(value.get("id") or self.identity(path)),
                str(value.get("name") or path.name or str(path)),
                path,
            )
            self._entries[entry.workspace_id] = entry
        selected = raw.get("selected")
        if isinstance(selected, str) and selected in self._entries:
            self.selected = selected

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "selected": self.selected,
            "workspaces": [entry.to_dict() for entry in self.list()],
        }
        temporary = self.path.with_suffix(f".tmp-{uuid4().hex}")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def list(self) -> tuple[WorkspaceEntry, ...]:
        return tuple(sorted(self._entries.values(), key=lambda item: item.name.casefold()))

    def add(self, path: Path, *, select: bool = True) -> WorkspaceEntry:
        resolved = path.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("workspace is not a directory")
        workspace_id = self.identity(resolved)
        entry = WorkspaceEntry(workspace_id, resolved.name or str(resolved), resolved)
        self._entries[workspace_id] = entry
        if select:
            self.selected = workspace_id
        self._write()
        return entry

    def remove(self, workspace_id: str) -> WorkspaceEntry:
        entry = self._entries.pop(workspace_id)
        if self.selected == workspace_id:
            self.selected = next(iter(self._entries), None)
        self._write()
        return entry

    def select(self, workspace_id: str) -> WorkspaceEntry:
        entry = self._entries[workspace_id]
        self.selected = workspace_id
        self._write()
        return entry

    def get(self, workspace_id: str | None = None) -> WorkspaceEntry:
        selected = workspace_id or self.selected
        if selected is None:
            raise KeyError("no workspace selected")
        return self._entries[selected]


class EventHub:
    def __init__(self, *, capacity: int = 4096) -> None:
        self._sequence = 0
        self._history: deque[dict[str, object]] = deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, envelope: dict[str, object]) -> dict[str, object]:
        async with self._lock:
            self._sequence += 1
            value = {"stream_sequence": self._sequence, **envelope}
            self._history.append(value)
            for subscriber in tuple(self._subscribers):
                subscriber.put_nowait(value)
            return value

    async def subscribe(self, after: int = 0) -> AsyncGenerator[dict[str, object], None]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        async with self._lock:
            backlog = tuple(
                item
                for item in self._history
                if isinstance(item.get("stream_sequence"), int)
                and cast(int, item["stream_sequence"]) > after
            )
            self._subscribers.add(queue)
        try:
            for item in backlog:
                yield item
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)


class WorkspaceRuntime:
    def __init__(self, entry: WorkspaceEntry, *, state_root: Path | None = None) -> None:
        self.entry = entry
        self.client: Windcode | None = None
        self.provider_service: ProviderService | None = None
        self.config_file = entry.path / ".windcode" / "config.toml"
        self.events = EventHub()
        self.runs: dict[str, RunHandle] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._state_root = state_root

    async def open(self) -> None:
        if self.client is not None:
            return
        config = load_config(self.entry.path, tolerate_provider_errors=True)
        client = Windcode.open(config, workspace=self.entry.path, state_root=self._state_root)
        await client.__aenter__()
        self.client = client
        self.provider_service = client.create_provider_service(config_file=self.config_file)

    def require_client(self) -> Windcode:
        if self.client is None:
            raise RuntimeError("workspace runtime is not open")
        return self.client

    def require_providers(self) -> ProviderService:
        if self.provider_service is None:
            raise RuntimeError("provider service is not open")
        self.provider_service.update_config(self.require_client().config)
        return self.provider_service

    async def start_run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        model: str | None,
        permission_mode: str | None,
    ) -> tuple[str, str]:
        client = self.require_client()
        handle = client.start_run(
            RunRequest(
                prompt,
                self.entry.path,
                session_id=session_id,
                model=model,
                permission_mode=permission_mode,
            )
        )
        started: asyncio.Future[tuple[str, str]] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._consume(handle, started))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await started

    async def _consume(self, handle: RunHandle, started: asyncio.Future[tuple[str, str]]) -> None:
        active_run_id: str | None = None
        active_session_id: str | None = None
        active_sdk_sequence: int | None = None
        try:
            async for event in handle:
                active_run_id = event.run_id
                active_session_id = event.session_id
                active_sdk_sequence = event.sequence
                self.runs[event.run_id] = handle
                if not started.done():
                    started.set_result((event.session_id, event.run_id))
                await self._publish_event(event)
            result = await handle.result()
            await self.events.publish(
                {
                    "type": "run.finished",
                    "workspace_id": self.entry.workspace_id,
                    "session_id": active_session_id,
                    "run_id": active_run_id,
                    "sdk_sequence": active_sdk_sequence,
                    "payload": json_value(result),
                }
            )
        except asyncio.CancelledError:
            if not started.done():
                started.cancel()
            raise
        except Exception as exc:
            if not started.done():
                started.set_exception(exc)
            await self.events.publish(
                {
                    "type": "error",
                    "workspace_id": self.entry.workspace_id,
                    "session_id": active_session_id,
                    "run_id": active_run_id,
                    "sdk_sequence": active_sdk_sequence,
                    "payload": {"message": str(exc), "error_type": type(exc).__name__},
                }
            )
        finally:
            if active_run_id is not None:
                self.runs.pop(active_run_id, None)

    async def _publish_event(self, event: AgentEventType) -> None:
        payload = event_to_dict(event)
        await self.events.publish(
            {
                "type": "run.event",
                "workspace_id": self.entry.workspace_id,
                "session_id": event.session_id,
                "run_id": event.run_id,
                "sdk_sequence": event.sequence,
                "payload": payload,
            }
        )

    def get_run(self, run_id: str) -> RunHandle:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise KeyError(f"unknown active run: {run_id}") from exc

    async def close(self) -> None:
        if self.client is None:
            return
        await self.client.aclose()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        self._tasks.clear()
        self.runs.clear()
        self.client = None
        self.provider_service = None


class WebRuntimeManager:
    def __init__(self, store: WorkspaceStore, *, state_root: Path | None = None) -> None:
        self.store = store
        self._runtimes: dict[str, WorkspaceRuntime] = {}
        self._lock = asyncio.Lock()
        self._state_root = state_root

    async def get(self, workspace_id: str | None = None) -> WorkspaceRuntime:
        entry = self.store.get(workspace_id)
        async with self._lock:
            runtime = self._runtimes.get(entry.workspace_id)
            if runtime is None:
                runtime = WorkspaceRuntime(entry, state_root=self._state_root)
                await runtime.open()
                self._runtimes[entry.workspace_id] = runtime
            return runtime

    async def remove(self, workspace_id: str) -> WorkspaceEntry:
        runtime = self._runtimes.pop(workspace_id, None)
        if runtime is not None:
            await runtime.close()
        return self.store.remove(workspace_id)

    async def close(self) -> None:
        await asyncio.gather(*(runtime.close() for runtime in self._runtimes.values()))
        self._runtimes.clear()
