import asyncio
import sys

import pytest

from windcode.extensions.mcp.client import McpClient, ResolvedHttpServer, ResolvedStdioServer


class FailingOwnerClient(McpClient):
    def install_failure(self) -> None:
        self._close_requested = asyncio.Event()

        async def fail() -> None:
            raise ExceptionGroup("background failure", [RuntimeError("unauthorized")])

        self._owner_task = asyncio.create_task(fail())


@pytest.mark.asyncio
async def test_close_absorbs_existing_owner_task_failure() -> None:
    client = FailingOwnerClient(ResolvedHttpServer("http://127.0.0.1/unused"))
    client.install_failure()
    await asyncio.sleep(0)

    await client.aclose()

    assert isinstance(client.close_error, ExceptionGroup)
    assert not client.connected


@pytest.mark.asyncio
async def test_close_cancels_ready_future_during_initialization() -> None:
    client = McpClient(
        ResolvedStdioServer(
            sys.executable,
            ("-c", "import time; time.sleep(60)"),
        ),
        connect_timeout=10,
    )
    connect_task = asyncio.create_task(client.connect())
    await asyncio.sleep(0)
    ready = client._ready  # pyright: ignore[reportPrivateUsage]
    assert ready is not None
    await asyncio.sleep(0)
    client.connect_timeout = 0.05

    connect_task.cancel()
    await asyncio.gather(connect_task, return_exceptions=True)
    await client.aclose()

    assert ready.cancelled()
    assert not client.connected
