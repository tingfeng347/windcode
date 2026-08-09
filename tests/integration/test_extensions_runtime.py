import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from windcode import Windcode
from windcode.domain.events import RunRequest
from windcode.domain.messages import TextBlock, ToolResultBlock
from windcode.domain.models import (
    ModelCompleted,
    ModelEvent,
    ModelRequest,
    StopReason,
    TextDelta,
    ToolCallDelta,
)


class SnapshotTransport:
    name = "snapshot"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            self.first_started.set()
            await self.release_first.wait()
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


class SkillLoadingTransport:
    name = "skill-loading"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield ToolCallDelta("load", "load_skill", '{"name":"review"}')
            yield ModelCompleted(StopReason.TOOL_USE)
            return
        yield TextDelta("done")
        yield ModelCompleted(StopReason.STOP)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_active_run_keeps_old_snapshot_while_new_run_observes_reload(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "extensions" / "complete_plugin"
    transport = SnapshotTransport()
    async with Windcode.open(
        {"extensions": {"enabled": True}},
        state_root=tmp_path / "state",
        workspace=tmp_path,
    ) as client:
        client.register_transport("snapshot", "model", transport, primary=True)
        await client.install_extension(fixture, enable=True)
        await client.reload_extensions()
        enabled_generation = client.extension_snapshot.generation

        old_handle = client.start_run(RunRequest("$review old", tmp_path))
        await asyncio.wait_for(transport.first_started.wait(), timeout=5)
        await client.set_extension_enabled("plugin:complete", False)
        await client.reload_extensions()
        assert client.extension_snapshot.generation > enabled_generation
        transport.release_first.set()
        await old_handle.result()

        new_handle = client.start_run(RunRequest("new", tmp_path))
        await new_handle.result()

    old_sources = {
        message.provider_metadata.get("extension_source")
        for message in transport.requests[0].messages
    }
    new_sources = {
        message.provider_metadata.get("extension_source")
        for message in transport.requests[1].messages
    }
    assert any(
        isinstance(source, str) and source.startswith("plugin:complete") for source in old_sources
    )
    assert not any(
        isinstance(source, str) and source.startswith("plugin:complete") for source in new_sources
    )
    assert "review" in transport.requests[0].system_prompt
    assert "review" not in transport.requests[1].system_prompt


@pytest.mark.asyncio
async def test_mcp_direct_tools_are_isolated_across_disable_reload(tmp_path: Path) -> None:
    server = Path(__file__).parents[1] / "contract" / "mcp" / "server.py"
    transport = SnapshotTransport()
    config = {
        "extensions": {
            "enabled": True,
            "direct_tool_limit": 2,
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

    async with Windcode.open(config, state_root=tmp_path / "state") as client:
        await client.wait_for_required_mcp()
        client.register_transport("snapshot", "model", transport, primary=True)

        old_handle = client.start_run(RunRequest("old generation", tmp_path))
        await asyncio.wait_for(transport.first_started.wait(), timeout=5)

        await client.set_extension_enabled("mcp_server:contract", False)
        await client.reload_extensions()
        new_handle = client.start_run(RunRequest("new generation", tmp_path))
        await asyncio.wait_for(new_handle.result(), timeout=5)

        transport.release_first.set()
        await asyncio.wait_for(old_handle.result(), timeout=5)

    old_tools = {tool.name for tool in transport.requests[0].tools}
    new_tools = {tool.name for tool in transport.requests[1].tools}
    assert "mcp_echo" in old_tools
    assert "mcp_echo" not in new_tools
    assert "contract" in transport.requests[0].system_prompt
    assert "contract" not in transport.requests[1].system_prompt


@pytest.mark.asyncio
async def test_model_can_load_skill_into_next_model_step(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "extensions" / "complete_plugin"
    transport = SkillLoadingTransport()
    async with Windcode.open(
        {"extensions": {"enabled": True}},
        state_root=tmp_path / "state",
        workspace=tmp_path,
    ) as client:
        client.register_transport("skill-loading", "model", transport, primary=True)
        await client.install_extension(fixture, enable=True)
        await client.reload_extensions()

        result = await client.start_run(RunRequest("inspect changes", tmp_path)).result()

    assert result.final_text == "done"
    assert {tool.name for tool in transport.requests[0].tools} >= {
        "search_skills",
        "load_skill",
    }
    tool_result_text = "\n".join(
        block.content
        for message in transport.requests[1].messages
        for block in message.content
        if isinstance(block, ToolResultBlock) and message.role.value == "tool"
    )
    assert '"status": "loaded"' in tool_result_text
    assert "correctness risks" not in tool_result_text

    sourced = [
        message
        for message in transport.requests[1].messages
        if message.provider_metadata.get("extension_source") is not None
    ]
    assert sourced[0].provider_metadata["extension_source"].startswith("plugin:complete")
    assert any(
        "correctness risks" in block.text
        for block in sourced[0].content
        if isinstance(block, TextBlock)
    )
