import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode import Windcode
from windcode.domain.events import ExtensionEvent, RunRequest
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
)


class McpCallingTransport:
    name = "mcp-caller"

    def __init__(self) -> None:
        self.calls = 0
        self.first_tools: tuple[str, ...] = ()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        if self.calls == 1:
            self.first_tools = tuple(tool.name for tool in request.tools)
            yield ToolCallDelta(
                "echo-call",
                "mcp_echo",
                json.dumps({"text": "integration"}),
            )
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class ToolCaptureTransport:
    name = "tool-capture"

    def __init__(self) -> None:
        self.tools: tuple[str, ...] = ()
        self.system_prompt = ""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.tools = tuple(tool.name for tool in request.tools)
        self.system_prompt = request.system_prompt
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_required_stdio_mcp_direct_exposure_events_and_cleanup(tmp_path: Path) -> None:
    server = Path(__file__).parents[1] / "contract" / "mcp" / "server.py"
    config = {
        "extensions": {
            "enabled": True,
            "direct_tool_limit": 2,
            "project_mcp_servers": ["contract"],
            "mcp_servers": {
                "contract": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(server)],
                    "required": True,
                }
            },
        }
    }
    transport = McpCallingTransport()
    async with Windcode.open(config, state_root=tmp_path / "state", workspace=tmp_path) as client:
        configured = (await client.inspect_extension("mcp_server:contract"))[0]
        assert configured.trusted
        await client.wait_for_required_mcp()
        client.register_transport("mcp", "model", transport, primary=True)
        handle = client.start_run(RunRequest("call MCP", tmp_path, permission_mode="full_access"))
        events: list[ExtensionEvent] = []
        async for event in handle:
            if isinstance(event, ExtensionEvent):
                events.append(event)
        result = await handle.result()

    assert result.final_text == "done"
    assert "mcp_echo" in transport.first_tools
    # Required servers connect once at client startup and remain open across runs.
    assert {event.action for event in events} >= {"mcp_called"}
    assert "mcp_closed" not in {event.action for event in events}
    assert all(event.server_id == "contract" for event in events)
    assert all(event.snapshot_generation == 1 for event in events)


@pytest.mark.asyncio
async def test_disabled_mcp_is_manageable_but_not_connected_or_searchable(tmp_path: Path) -> None:
    config = {
        "extensions": {
            "enabled": True,
            "mcp_servers": {
                "hidden-mcp-marker": {
                    "transport": "stdio",
                    "command": "must-not-start",
                    "enable": False,
                    "required": True,
                }
            },
        }
    }
    transport = ToolCaptureTransport()

    async with Windcode.open(config, state_root=tmp_path / "state", workspace=tmp_path) as client:
        client.register_transport("capture", "model", transport, primary=True)
        result = await client.start_run(RunRequest("do nothing", tmp_path)).result()
        record = (await client.inspect_extension("mcp_server:hidden-mcp-marker"))[0]

    assert result.final_text == "done"
    assert not record.enabled
    assert "list_mcp_servers" in transport.tools
    assert "search_mcp_tools" not in transport.tools
    assert "hidden-mcp-marker" not in transport.system_prompt
