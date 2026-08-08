from pathlib import Path

import jsonschema
import pytest
from mcp.types import CallToolResult, TextContent

from windcode.domain.tools import ToolContext
from windcode.extensions.mcp.adapter import McpToolAdapter
from windcode.extensions.mcp.catalog import McpToolDefinition
from windcode.extensions.mcp.runtime import McpRuntime
from windcode.extensions.mcp.tools import McpCapabilityService
from windcode.sessions.artifacts import ArtifactStore


class ResultRuntime(McpRuntime):
    def __init__(self, result: CallToolResult) -> None:
        super().__init__({})
        self.result = result

    async def call(self, server_id: str, operation: object) -> object:
        del server_id, operation
        return self.result


@pytest.mark.asyncio
async def test_adapter_preserves_schema_and_externalizes_large_result(tmp_path: Path) -> None:
    definition = McpToolDefinition(
        "mcp:server/tool/echo",
        "server",
        "echo",
        "Echo",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        {},
    )
    runtime = ResultRuntime(CallToolResult(content=[TextContent(type="text", text="x" * 100)]))
    adapter = McpToolAdapter(
        definition, runtime, artifact_store=ArtifactStore(tmp_path), output_limit=20
    )
    arguments = adapter.validate_arguments({"text": "hello"})
    result = await adapter.execute(ToolContext(tmp_path, "run", lambda: False), arguments)

    assert not result.is_error
    assert result.artifact_ref is not None
    assert "full output" in result.output
    assert adapter.input_schema == definition.input_schema


@pytest.mark.asyncio
async def test_adapter_does_not_duplicate_mirrored_structured_content(tmp_path: Path) -> None:
    definition = McpToolDefinition(
        "mcp:server/tool/echo", "server", "echo", "Echo", {"type": "object"}, {}
    )
    result = CallToolResult(
        content=[TextContent(type="text", text="hello")],
        structuredContent={"result": "hello"},
    )
    adapter = McpToolAdapter(definition, ResultRuntime(result))

    actual = await adapter.execute(
        ToolContext(tmp_path, "run", lambda: False), adapter.validate_arguments({})
    )

    assert actual.output == "hello"


def test_adapter_rejects_complex_invalid_arguments() -> None:
    definition = McpToolDefinition(
        "mcp:s/tool/t",
        "s",
        "t",
        "",
        {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "integer"}}},
            "required": ["items"],
            "additionalProperties": False,
        },
        {},
    )
    adapter = McpToolAdapter(definition, ResultRuntime(CallToolResult(content=[])))
    with pytest.raises(jsonschema.ValidationError):
        adapter.validate_arguments({"items": ["bad"]})


@pytest.mark.asyncio
async def test_capability_service_attaches_plugin_origin_to_adapter() -> None:
    definition = McpToolDefinition(
        "mcp:server/tool/echo", "server", "echo", "Echo", {"type": "object"}, {}
    )
    service = McpCapabilityService(
        McpRuntime({"server": (lambda: pytest.fail("cached catalog must not connect"), False)}),
        tool_catalogs={"server": (definition,)},
        server_origins={"server": "plugin:review/mcp"},
    )

    adapter = await service.adapter(definition.stable_id)

    assert adapter.origin == "plugin:review/mcp"
