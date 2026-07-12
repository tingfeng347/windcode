from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

import jsonschema
from mcp.types import AudioContent, ImageContent, TextContent
from pydantic import BaseModel, RootModel

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.extensions.mcp.catalog import McpToolDefinition, mcp_tool_wire_name
from windcode.extensions.mcp.runtime import McpRuntime
from windcode.sessions.artifacts import ArtifactStore


class McpArguments(RootModel[dict[str, Any]]):
    pass


class McpToolAdapter:
    def __init__(
        self,
        definition: McpToolDefinition,
        runtime: McpRuntime,
        *,
        artifact_store: ArtifactStore | None = None,
        output_limit: int = 20_000,
    ) -> None:
        self.definition = definition
        self.runtime = runtime
        self.artifact_store = artifact_store
        self.output_limit = output_limit

    @property
    def name(self) -> str:
        return mcp_tool_wire_name(self.definition.server_id, self.definition.name)

    @property
    def description(self) -> str:
        return self.definition.description or f"MCP tool from {self.definition.server_id}"

    @property
    def input_model(self) -> type[BaseModel]:
        return McpArguments

    @property
    def input_schema(self) -> Mapping[str, Any]:
        return self.definition.input_schema

    @property
    def effects(self) -> frozenset[ToolEffect]:
        return frozenset({ToolEffect.PROCESS, ToolEffect.NETWORK})

    def validate_arguments(self, arguments: Mapping[str, Any]) -> McpArguments:
        jsonschema.Draft202012Validator(self.definition.input_schema).validate(arguments)  # pyright: ignore[reportUnknownMemberType]
        return McpArguments(dict(arguments))

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        if context.cancelled():
            return ToolResult("MCP call cancelled", True, data={"error": "cancelled"})
        parsed = cast(McpArguments, arguments)
        result = await self.runtime.call(
            self.definition.server_id,
            lambda client: client.call_tool(
                self.definition.name, cast(dict[str, object], parsed.root)
            ),
        )
        from mcp.types import CallToolResult

        if not isinstance(result, CallToolResult):
            raise ValueError("invalid MCP call result")
        parts: list[str] = []
        for item in result.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
            elif isinstance(item, (ImageContent, AudioContent)):
                parts.append(f"[{item.type}: {item.mimeType}; base64={item.data}]")
            else:
                parts.append(json.dumps(item.model_dump(mode="json"), sort_keys=True))
        # MCP servers commonly mirror structuredContent into text content for
        # backwards-compatible clients. Prefer the canonical content when present.
        if not parts and result.structuredContent is not None:
            parts.append(json.dumps(result.structuredContent, sort_keys=True))
        output = "\n".join(parts)
        artifact_ref: str | None = None
        if self.artifact_store is not None:
            output, reference = self.artifact_store.externalize(output, threshold=self.output_limit)
            artifact_ref = None if reference is None else reference.relative_path
        elif len(output) > self.output_limit:
            output = output[: self.output_limit] + "..."
        return ToolResult(
            output,
            is_error=result.isError,
            artifact_ref=artifact_ref,
            data={
                "source": self.definition.server_id,
                "tool": self.definition.stable_id,
                "error": "remote_error" if result.isError else None,
            },
        )
