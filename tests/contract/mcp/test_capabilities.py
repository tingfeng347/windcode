import sys
from pathlib import Path

import pytest

from windcode.extensions.mcp.client import McpClient, ResolvedStdioServer
from windcode.extensions.mcp.runtime import McpRuntime, McpServerState
from windcode.extensions.mcp.tools import McpCapabilityService
from windcode.sessions import ArtifactStore
from windcode.tools import ToolRegistry


@pytest.mark.asyncio
async def test_four_capability_classes_keep_stable_source_and_close(tmp_path: Path) -> None:
    server = Path(__file__).with_name("server.py")
    runtime = McpRuntime(
        {
            "contract": (
                lambda: McpClient(
                    ResolvedStdioServer(sys.executable, (str(server),)),
                    connect_timeout=10,
                    call_timeout=5,
                ),
                True,
            )
        }
    )
    service = McpCapabilityService(
        runtime, artifact_store=ArtifactStore(tmp_path), content_limit=4096
    )
    try:
        catalog = await service.catalog("contract")
        assert [tool.stable_id for tool in catalog.tools] == [
            "mcp:contract/tool/add",
            "mcp:contract/tool/echo",
        ]
        assert [resource.stable_id for resource in catalog.resources] == [
            "mcp:contract/resource/af49bd831dbabc39"
        ]
        assert [prompt.stable_id for prompt in catalog.prompts] == ["mcp:contract/prompt/review"]

        instructions = await service.instructions("contract")
        resource = await service.read_resource("contract", "memo://example")
        prompt = await service.get_prompt("contract", "review", {"topic": "runtime"})

        assert instructions is not None
        assert instructions.server_id == "contract"
        assert instructions.identity == "instructions"
        assert instructions.content == "Contract server instructions"
        assert resource.server_id == "contract"
        assert resource.identity == "memo://example"
        assert resource.content == "resource content"
        assert prompt.server_id == "contract"
        assert prompt.identity == "review"
        assert "Review runtime" in prompt.content
        assert service.drain_context()[0].source_id == "mcp:contract/instructions"

        activated_prompt = await service.activate_prompt("review")
        assert "Review code" in activated_prompt.content
        assert service.drain_context()[0].source_id == "mcp:contract/prompt/review"

        hidden_registry = ToolRegistry()
        assert await service.register_direct_tools(hidden_registry, direct_tool_limit=1) == ()
        assert hidden_registry.schemas() == ()

        direct_registry = ToolRegistry()
        assert await service.register_direct_tools(direct_registry, direct_tool_limit=2) == (
            "mcp_add",
            "mcp_echo",
        )
        assert {schema.name for schema in direct_registry.schemas()} == {
            "mcp_add",
            "mcp_echo",
        }
    finally:
        await runtime.aclose()

    assert runtime.state("contract") is McpServerState.CLOSED
