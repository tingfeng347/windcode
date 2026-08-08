import sys
from pathlib import Path

import pytest

from windcode.extensions.mcp.client import McpClient, ResolvedStdioServer


@pytest.mark.asyncio
async def test_stdio_initialize_capabilities_call_and_close() -> None:
    server = Path(__file__).with_name("server.py")
    client = McpClient(
        ResolvedStdioServer(sys.executable, (str(server),)),
        connect_timeout=10,
        call_timeout=5,
    )

    initialize = await client.connect()
    tools = await client.list_tools()
    resources = await client.list_resources()
    prompts = await client.list_prompts()
    result = await client.call_tool("echo", {"text": "hello"})

    assert initialize.instructions == "Contract server instructions"
    assert {tool.name for tool in tools.tools} == {"add", "echo"}
    assert [str(resource.uri) for resource in resources.resources] == ["memo://example"]
    assert [prompt.name for prompt in prompts.prompts] == ["review"]
    assert result.isError is False
    await client.aclose()
    assert not client.connected
