from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.extensions.mcp.tools import SearchMcpToolsInput, SearchMcpToolsTool
from windcode.tools import ToolRegistry


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FakeAdapter:
    name = "mcp_tavily_search"
    description = "search"
    input_model = EmptyInput
    effects = frozenset({ToolEffect.NETWORK})
    definition = SimpleNamespace(server_id="tavily-mcp")

    async def execute(self, context: object, arguments: BaseModel) -> ToolResult:
        del context, arguments
        return ToolResult("ok")


class FakeService:
    async def adapter(self, stable_id: str) -> FakeAdapter:
        assert stable_id == "mcp:tavily-mcp/tool/tavily_search"
        return FakeAdapter()


async def test_selected_mcp_tool_is_registered_in_child_registry() -> None:
    root = ToolRegistry()
    child = ToolRegistry()
    tool = SearchMcpToolsTool(
        cast(Any, FakeService()),
        root,
        set(),
    )
    tool.add_registry(child)

    await tool.execute(
        ToolContext(Path.cwd(), "run", lambda: False),
        SearchMcpToolsInput(query="select:mcp:tavily-mcp/tool/tavily_search"),
    )

    assert "mcp_tavily_search" in root.names()
    assert "mcp_tavily_search" in child.names()
