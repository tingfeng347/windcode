from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from windcode.extensions.mcp.client import McpClient


class McpServerState(StrEnum):
    DISCOVERED = "discovered"
    CONNECTING = "connecting"
    READY = "ready"
    FAILED = "failed"
    CLOSING = "closing"
    CLOSED = "closed"


ClientFactory = Callable[[], McpClient]
McpObserver = Callable[[str, str, str], Awaitable[None]]


@dataclass(slots=True)
class _ServerSlot:
    factory: ClientFactory
    required: bool
    state: McpServerState = McpServerState.DISCOVERED
    client: McpClient | None = None
    error: BaseException | None = None
    reconnects: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class McpRuntime:
    def __init__(
        self,
        servers: dict[str, tuple[ClientFactory, bool]],
        observer: McpObserver | None = None,
    ) -> None:
        self._servers = {
            server_id: _ServerSlot(factory, required)
            for server_id, (factory, required) in sorted(servers.items())
        }
        self._closed = False
        self._retirements: set[asyncio.Task[None]] = set()
        self.observer = observer

    async def _observe(self, action: str, server_id: str, status: str) -> None:
        if self.observer is not None:
            await self.observer(action, server_id, status)

    def state(self, server_id: str) -> McpServerState:
        return self._servers[server_id].state

    @property
    def server_ids(self) -> tuple[str, ...]:
        return tuple(self._servers)

    @property
    def required_server_ids(self) -> tuple[str, ...]:
        return tuple(server_id for server_id, slot in self._servers.items() if slot.required)

    async def activate(self, server_id: str) -> McpClient:
        slot = self._servers[server_id]
        async with slot.lock:
            if self._closed:
                raise RuntimeError("MCP runtime is closed")
            if slot.state is McpServerState.READY and slot.client is not None:
                if slot.client.connected:
                    return slot.client
                stale = slot.client
                slot.client = None
                slot.state = McpServerState.DISCOVERED
                self._schedule_retirement(server_id, stale)
            slot.state = McpServerState.CONNECTING
            await self._observe("mcp_connecting", server_id, "connecting")
            client: McpClient | None = None
            try:
                client = slot.factory()
                await client.connect()
            except BaseException as exc:
                slot.state = McpServerState.FAILED
                slot.error = exc
                await self._observe("diagnostic", server_id, "failed")
                if client is not None:
                    await client.aclose()
                raise
            slot.client = client
            slot.error = None
            slot.state = McpServerState.READY
            await self._observe("mcp_connected", server_id, "ready")
            return client

    def _schedule_retirement(self, server_id: str, client: McpClient) -> None:
        async def retire() -> None:
            try:
                await client.aclose()
                if client.close_error is not None:
                    await self._observe("diagnostic", server_id, "close_failed")
            except Exception:
                await self._observe("diagnostic", server_id, "close_failed")

        task = asyncio.create_task(retire())
        self._retirements.add(task)
        task.add_done_callback(self._retirement_done)

    def _retirement_done(self, task: asyncio.Task[None]) -> None:
        self._retirements.discard(task)
        if not task.cancelled():
            task.exception()

    async def _invalidate(self, server_id: str, client: McpClient) -> None:
        slot = self._servers[server_id]
        async with slot.lock:
            if slot.client is not client:
                return
            slot.client = None
            slot.state = McpServerState.CLOSED if self._closed else McpServerState.DISCOVERED
            self._schedule_retirement(server_id, client)

    @staticmethod
    def _connection_failed(exc: BaseException, client: McpClient) -> bool:
        return (
            not client.connected
            or isinstance(exc, (ConnectionError, EOFError, BrokenPipeError))
            or (isinstance(exc, RuntimeError) and str(exc) == "MCP client is not connected")
        )

    async def call(
        self, server_id: str, operation: Callable[[McpClient], Awaitable[object]]
    ) -> object:
        for attempt in range(2):
            client = await self.activate(server_id)
            try:
                result = await operation(client)
                await self._observe("mcp_called", server_id, "success")
                return result
            except asyncio.CancelledError:
                # Cancelling an in-flight MCP request can close or desynchronize the SDK session.
                # Retire it without delaying UI cancellation; the next turn gets a fresh client.
                await self._invalidate(server_id, client)
                raise
            except TimeoutError:
                # The remote operation may still have happened, so never retry a timed-out tool.
                await self._invalidate(server_id, client)
                await self._observe("diagnostic", server_id, "call_failed")
                raise
            except BaseException as exc:
                if attempt == 0 and self._connection_failed(exc, client):
                    self._servers[server_id].reconnects += 1
                    await self._invalidate(server_id, client)
                    continue
                await self._observe("diagnostic", server_id, "call_failed")
                raise
        raise RuntimeError("MCP call retry loop exhausted")

    async def activate_required(self, *, concurrency: int = 4) -> None:
        semaphore = asyncio.Semaphore(concurrency)

        async def activate_one(server_id: str) -> None:
            async with semaphore:
                await self.activate(server_id)

        await asyncio.gather(
            *(activate_one(server_id) for server_id, slot in self._servers.items() if slot.required)
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for server_id, slot in reversed(tuple(self._servers.items())):
            async with slot.lock:
                slot.state = McpServerState.CLOSING
                client = slot.client
                try:
                    if client is not None:
                        await client.aclose()
                        if client.close_error is not None:
                            await self._observe("diagnostic", server_id, "close_failed")
                except Exception:
                    await self._observe("diagnostic", server_id, "close_failed")
                finally:
                    slot.client = None
                    slot.state = McpServerState.CLOSED
                    await self._observe("mcp_closed", server_id, "closed")
        while self._retirements:
            await asyncio.gather(*tuple(self._retirements), return_exceptions=True)
