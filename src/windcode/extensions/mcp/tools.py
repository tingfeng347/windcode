from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import cast

from mcp.types import (
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    Prompt,
    Resource,
    ResourceTemplate,
    TextResourceContents,
    Tool,
)
from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.messages import SourcedContextMessage
from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.extensions.mcp.adapter import McpToolAdapter
from windcode.extensions.mcp.catalog import (
    McpCatalog,
    McpPromptDefinition,
    McpResourceDefinition,
    McpToolDefinition,
    build_catalog,
)
from windcode.extensions.mcp.runtime import McpRuntime
from windcode.extensions.models import CapabilityKind, CapabilityRecord
from windcode.sessions.artifacts import ArtifactStore
from windcode.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class McpToolSearchResult:
    stable_id: str
    server_id: str
    name: str
    description: str


class McpToolView:
    def __init__(
        self,
        catalogs: tuple[McpCatalog, ...],
        adapters: dict[str, McpToolAdapter],
        *,
        direct_tool_limit: int,
    ) -> None:
        self._definitions = {tool.stable_id: tool for catalog in catalogs for tool in catalog.tools}
        self._adapters = dict(adapters)
        self.direct_tool_limit = direct_tool_limit
        self._selected: set[str] = set()

    def search(self, query: str = "") -> tuple[McpToolSearchResult, ...]:
        needle = query.casefold().strip()
        return tuple(
            McpToolSearchResult(item.stable_id, item.server_id, item.name, item.description)
            for item in sorted(self._definitions.values(), key=lambda value: value.stable_id)
            if not needle or needle in item.name.casefold() or needle in item.description.casefold()
        )

    def select(self, stable_id: str) -> McpToolDefinition:
        try:
            definition = self._definitions[stable_id.removeprefix("select:")]
        except KeyError as exc:
            raise KeyError(f"unknown MCP tool: {stable_id}") from exc
        self._selected.add(definition.stable_id)
        return definition

    def register_visible(self, registry: ToolRegistry) -> None:
        visible = (
            set(self._definitions)
            if len(self._definitions) <= self.direct_tool_limit
            else self._selected
        )
        for stable_id in sorted(visible):
            registry.register(self._adapters[stable_id], replace=True)


@dataclass(frozen=True, slots=True)
class SourcedMcpContent:
    server_id: str
    identity: str
    content: str
    artifact_ref: str | None = None


class McpCapabilityService:
    def __init__(
        self,
        runtime: McpRuntime,
        *,
        artifact_store: ArtifactStore | None = None,
        content_limit: int = 20_000,
        tool_catalogs: dict[str, tuple[McpToolDefinition, ...]] | None = None,
    ) -> None:
        self.runtime = runtime
        self.artifact_store = artifact_store
        self.content_limit = content_limit
        self._catalogs: dict[str, McpCatalog] = {}
        self._tool_catalogs = tool_catalogs if tool_catalogs is not None else {}
        self._tool_catalog_locks: dict[str, asyncio.Lock] = {}
        self._instructions_emitted: set[str] = set()
        self._pending_context: list[SourcedContextMessage] = []

    def drain_context(self) -> tuple[SourcedContextMessage, ...]:
        messages = tuple(self._pending_context)
        self._pending_context.clear()
        return messages

    def _emit_instructions(self, server_id: str, instructions: str | None) -> None:
        if not instructions or server_id in self._instructions_emitted:
            return
        self._instructions_emitted.add(server_id)
        self._pending_context.append(
            SourcedContextMessage(f"mcp:{server_id}/instructions", instructions)
        )

    async def catalog(self, server_id: str) -> McpCatalog:
        cached = self._catalogs.get(server_id)
        if cached is not None:
            return cached
        client = await self.runtime.activate(server_id)
        initialize = client.initialize_result
        if initialize is None:
            raise RuntimeError("MCP Server did not initialize")
        tool_values: list[Tool] = []
        resource_values: list[Resource] = []
        template_values: list[ResourceTemplate] = []
        prompt_values: list[Prompt] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor)
            tool_values.extend(page.tools)
            cursor = page.nextCursor
            if cursor is None:
                break
        cursor = None
        while True:
            resource_page = await client.list_resources(cursor)
            resource_values.extend(resource_page.resources)
            cursor = resource_page.nextCursor
            if cursor is None:
                break
        cursor = None
        while True:
            template_page = await client.list_resource_templates(cursor)
            template_values.extend(template_page.resourceTemplates)
            cursor = template_page.nextCursor
            if cursor is None:
                break
        cursor = None
        while True:
            prompt_page = await client.list_prompts(cursor)
            prompt_values.extend(prompt_page.prompts)
            cursor = prompt_page.nextCursor
            if cursor is None:
                break
        catalog = build_catalog(
            server_id,
            initialize,
            ListToolsResult(tools=tool_values),
            ListResourcesResult(resources=resource_values),
            ListPromptsResult(prompts=prompt_values),
            ListResourceTemplatesResult(resourceTemplates=template_values),
        )
        self._catalogs[server_id] = catalog
        self._tool_catalogs[server_id] = catalog.tools
        self._emit_instructions(server_id, catalog.instructions)
        return catalog

    async def tool_catalog(self, server_id: str) -> tuple[McpToolDefinition, ...]:
        """List a server's tools without fetching resources/templates/prompts.

        Tool discovery (search_mcp_tools) and direct registration only need
        tools, so avoid the extra network round-trips a full catalog build makes.
        """

        full = self._catalogs.get(server_id)
        if full is not None:
            return full.tools
        cached = self._tool_catalogs.get(server_id)
        if cached is not None:
            return cached
        lock = self._tool_catalog_locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            cached = self._tool_catalogs.get(server_id)
            if cached is not None:
                return cached
            client = await self.runtime.activate(server_id)
            initialize = client.initialize_result
            if initialize is None:
                raise RuntimeError("MCP Server did not initialize")
            tool_values: list[Tool] = []
            cursor: str | None = None
            while True:
                page = await client.list_tools(cursor)
                tool_values.extend(page.tools)
                cursor = page.nextCursor
                if cursor is None:
                    break
            catalog = build_catalog(
                server_id,
                initialize,
                ListToolsResult(tools=tool_values),
                ListResourcesResult(resources=[]),
                ListPromptsResult(prompts=[]),
            )
            self._tool_catalogs[server_id] = catalog.tools
            self._emit_instructions(server_id, catalog.instructions)
            return catalog.tools

    async def search_tools(self, query: str = "") -> tuple[McpToolDefinition, ...]:
        tool_lists = [await self.tool_catalog(server_id) for server_id in self.runtime.server_ids]
        needle = query.casefold().strip()
        return tuple(
            tool
            for tools in tool_lists
            for tool in tools
            if not needle or needle in tool.name.casefold() or needle in tool.description.casefold()
        )

    async def tool(self, stable_id: str) -> McpToolDefinition:
        for definition in await self.search_tools():
            if definition.stable_id == stable_id:
                return definition
        raise KeyError(f"unknown MCP tool: {stable_id}")

    async def adapter(self, stable_id: str) -> McpToolAdapter:
        definition = await self.tool(stable_id)
        return McpToolAdapter(
            definition,
            self.runtime,
            artifact_store=self.artifact_store,
            output_limit=self.content_limit,
        )

    async def register_selected_tools(
        self, registry: ToolRegistry, stable_ids: set[str]
    ) -> tuple[str, ...]:
        registered: list[str] = []
        stale: list[str] = []
        for stable_id in sorted(stable_ids):
            try:
                adapter = await self.adapter(stable_id)
            except KeyError:
                stale.append(stable_id)
                continue
            registry.register(adapter, replace=True)
            registered.append(adapter.name)
        stable_ids.difference_update(stale)
        return tuple(registered)

    async def register_direct_tools(
        self, registry: ToolRegistry, *, direct_tool_limit: int
    ) -> tuple[str, ...]:
        tool_lists = [
            await self.tool_catalog(server_id) for server_id in self.runtime.required_server_ids
        ]
        definitions = tuple(tool for tools in tool_lists for tool in tools)
        if len(definitions) > direct_tool_limit:
            return ()
        registered: list[str] = []
        for definition in sorted(definitions, key=lambda item: item.stable_id):
            adapter = McpToolAdapter(
                definition,
                self.runtime,
                artifact_store=self.artifact_store,
                output_limit=self.content_limit,
            )
            registry.register(adapter, replace=True)
            registered.append(adapter.name)
        return tuple(registered)

    async def resources(self, server_id: str) -> tuple[McpResourceDefinition, ...]:
        return (await self.catalog(server_id)).resources

    async def prompts(self, server_id: str) -> tuple[McpPromptDefinition, ...]:
        return (await self.catalog(server_id)).prompts

    async def read_resource(self, server_id: str, uri: str) -> SourcedMcpContent:
        result = await (await self.runtime.activate(server_id)).read_resource(uri)
        content = "\n".join(
            item.text
            if isinstance(item, TextResourceContents)
            else json.dumps(item.model_dump(mode="json"), sort_keys=True)
            for item in result.contents
        )
        return self._bounded(server_id, uri, content)

    async def get_prompt(
        self, server_id: str, name: str, arguments: dict[str, str] | None = None
    ) -> SourcedMcpContent:
        result = await (await self.runtime.activate(server_id)).get_prompt(name, arguments)
        content = "\n".join(
            json.dumps(message.model_dump(mode="json"), sort_keys=True)
            for message in result.messages
        )
        return self._bounded(server_id, name, content)

    async def activate_prompt(self, name: str) -> SourcedMcpContent:
        matches: list[tuple[str, McpPromptDefinition]] = []
        for server_id in self.runtime.server_ids:
            matches.extend(
                (server_id, prompt)
                for prompt in await self.prompts(server_id)
                if prompt.name == name or prompt.stable_id == name
            )
        if not matches:
            raise KeyError(f"unknown MCP prompt: {name}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous MCP prompt: {name}")
        server_id, prompt = matches[0]
        content = await self.get_prompt(server_id, prompt.name)
        self._pending_context.append(
            SourcedContextMessage(f"mcp:{server_id}/prompt/{prompt.name}", content.content)
        )
        return content

    async def instructions(self, server_id: str) -> SourcedMcpContent | None:
        instructions = (await self.catalog(server_id)).instructions
        return (
            None if instructions is None else self._bounded(server_id, "instructions", instructions)
        )

    def _bounded(self, server_id: str, identity: str, content: str) -> SourcedMcpContent:
        if self.artifact_store is None:
            return SourcedMcpContent(server_id, identity, content[: self.content_limit])
        summary, reference = self.artifact_store.externalize(content, threshold=self.content_limit)
        return SourcedMcpContent(
            server_id,
            identity,
            summary,
            None if reference is None else reference.relative_path,
        )


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchMcpToolsInput(_StrictInput):
    query: str = ""


class ListMcpServersInput(_StrictInput):
    include_disabled: bool = False


class ListMcpServersTool:
    name = "list_mcp_servers"
    description = (
        "List enabled MCP servers available to this configuration. Disabled servers are omitted "
        "by default and must not be counted as current MCP servers. Set `include_disabled` to "
        "true only when the user explicitly asks to inspect disabled MCP configuration."
    )
    input_model = ListMcpServersInput
    effects = frozenset[ToolEffect]()

    def __init__(
        self,
        records: tuple[CapabilityRecord, ...],
        tool_catalogs: dict[str, tuple[McpToolDefinition, ...]],
        selected_tools: set[str],
    ) -> None:
        self.records = tuple(
            record for record in records if record.kind is CapabilityKind.MCP_SERVER
        )
        self.tool_catalogs = tool_catalogs
        self.selected_tools = selected_tools

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(ListMcpServersInput, arguments)
        visible_records = tuple(
            record for record in self.records if parsed.include_disabled or record.enabled
        )
        return ToolResult(
            json.dumps(
                {
                    "server_count": len(visible_records),
                    "includes_disabled": parsed.include_disabled,
                    "servers": [
                        {
                            "id": record.public_name,
                            "enabled": record.enabled,
                            "trusted": record.trusted,
                            "required": record.required,
                            "activation": record.activation.value,
                            "scope": record.source.scope.value,
                            "available_this_run": record.enabled and record.trusted,
                            "tool_catalog_cached": record.public_name in self.tool_catalogs,
                            "cached_tool_count": len(
                                self.tool_catalogs.get(record.public_name, ())
                            ),
                            "selected_tool_count": sum(
                                tool_id.startswith(f"mcp:{record.public_name}/tool/")
                                for tool_id in self.selected_tools
                            ),
                        }
                        for record in visible_records
                    ],
                },
                sort_keys=True,
            )
        )


class ReadMcpResourceInput(_StrictInput):
    server_id: str = Field(min_length=1)
    uri: str = Field(min_length=1)


class GetMcpPromptInput(_StrictInput):
    server_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, str] = Field(default_factory=dict[str, str])


class SearchMcpToolsTool:
    name = "search_mcp_tools"
    description = (
        "Discover and enable an MCP tool that is not already available. Search with a specific "
        "keyword: one match is enabled immediately and returns its `call_name`; multiple matches "
        "require one follow-up query using `select:<id>`. Call the returned tool directly and do "
        "not repeat discovery. Enabled tools remain available in later runs."
    )
    input_model = SearchMcpToolsInput
    effects = frozenset({ToolEffect.PROCESS, ToolEffect.NETWORK})

    def __init__(
        self,
        service: McpCapabilityService,
        registry: ToolRegistry,
        selected_tools: set[str],
    ) -> None:
        self.service = service
        self.registries = [registry]
        self.selected_tools = selected_tools

    def add_registry(self, registry: ToolRegistry) -> None:
        if all(item is not registry for item in self.registries):
            self.registries.append(registry)

    async def _select(self, stable_id: str) -> tuple[McpToolAdapter, bool]:
        already_selected = stable_id in self.selected_tools
        adapter = await self.service.adapter(stable_id)
        for registry in self.registries:
            registry.register(adapter, replace=True)
        self.selected_tools.add(stable_id)
        return adapter, already_selected

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(SearchMcpToolsInput, arguments)
        if parsed.query.startswith("select:"):
            stable_id = parsed.query.removeprefix("select:")
            adapter, already_selected = await self._select(stable_id)
            return ToolResult(
                json.dumps(
                    {
                        "selected": stable_id,
                        "already_selected": already_selected,
                        "call_name": adapter.name,
                        "source": adapter.definition.server_id,
                        "next_step": (
                            f"call the tool named {adapter.name} directly with its arguments"
                        ),
                    },
                    sort_keys=True,
                )
            )
        tools = await self.service.search_tools(parsed.query)
        if len(tools) == 1:
            tool = tools[0]
            adapter, already_selected = await self._select(tool.stable_id)
            return ToolResult(
                json.dumps(
                    {
                        "selected": tool.stable_id,
                        "already_selected": already_selected,
                        "call_name": adapter.name,
                        "source": tool.server_id,
                        "next_step": (
                            f"call the tool named {adapter.name} directly; do not call "
                            "search_mcp_tools again"
                        ),
                    },
                    sort_keys=True,
                )
            )
        return ToolResult(
            json.dumps(
                {
                    "tools": [
                        {
                            "id": tool.stable_id,
                            "name": tool.name,
                            "description": tool.description,
                            "source": tool.server_id,
                        }
                        for tool in tools
                    ],
                    "hint": (
                        "Multiple tools matched. Enable the intended one with "
                        "query='select:<id>' exactly once, then call the returned call_name."
                    ),
                },
                sort_keys=True,
            )
        )


class ReadMcpResourceTool:
    name = "read_mcp_resource"
    description = "Read a resource from an activated MCP Server with source attribution."
    input_model = ReadMcpResourceInput
    effects = frozenset({ToolEffect.PROCESS, ToolEffect.NETWORK})

    def __init__(self, service: McpCapabilityService) -> None:
        self.service = service

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(ReadMcpResourceInput, arguments)
        content = await self.service.read_resource(parsed.server_id, parsed.uri)
        return ToolResult(
            content.content,
            artifact_ref=content.artifact_ref,
            data={"source": content.server_id, "identity": content.identity},
        )


class GetMcpPromptTool:
    name = "get_mcp_prompt"
    description = "Get a prompt from an activated MCP Server with source attribution."
    input_model = GetMcpPromptInput
    effects = frozenset({ToolEffect.PROCESS, ToolEffect.NETWORK})

    def __init__(self, service: McpCapabilityService) -> None:
        self.service = service

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        parsed = cast(GetMcpPromptInput, arguments)
        content = await self.service.get_prompt(parsed.server_id, parsed.name, parsed.arguments)
        return ToolResult(
            content.content,
            artifact_ref=content.artifact_ref,
            data={"source": content.server_id, "identity": content.identity},
        )


def register_mcp_management_tools(
    registry: ToolRegistry, service: McpCapabilityService, selected_tools: set[str]
) -> None:
    for tool in (
        SearchMcpToolsTool(service, registry, selected_tools),
        ReadMcpResourceTool(service),
        GetMcpPromptTool(service),
    ):
        registry.register(tool)


def register_mcp_status_tool(
    registry: ToolRegistry,
    records: tuple[CapabilityRecord, ...],
    tool_catalogs: dict[str, tuple[McpToolDefinition, ...]],
    selected_tools: set[str],
) -> None:
    registry.register(ListMcpServersTool(records, tool_catalogs, selected_tools))
