import asyncio
import socket
import sys
from pathlib import Path

import pytest

from windcode.extensions.mcp.client import McpClient, ResolvedHttpServer


def _free_port() -> int:
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


async def _wait_for_server(port: int) -> None:
    for _ in range(100):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            del reader
            return
        except OSError:
            await asyncio.sleep(0.02)
    raise TimeoutError("contract HTTP server did not start")


@pytest.mark.asyncio
async def test_streamable_http_initialize_call_and_close() -> None:
    port = _free_port()
    server = Path(__file__).with_name("server.py")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(server),
        "--transport",
        "streamable-http",
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _wait_for_server(port)
        client = McpClient(
            ResolvedHttpServer(f"http://127.0.0.1:{port}/mcp"),
            connect_timeout=5,
            call_timeout=5,
        )
        await client.connect()
        result = await client.call_tool("add", {"left": 2, "right": 3})
        assert not result.isError
        await client.aclose()
        assert not client.connected
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5)
