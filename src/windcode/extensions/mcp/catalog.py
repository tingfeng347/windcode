from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from mcp.types import (
    InitializeResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
)

from windcode.extensions.models import normalize_id

# Provider function-calling names allow only these characters, capped at 64.
# MCP stable_ids ("mcp:{server}/tool/{name}") contain ":" and "/", and tool
# names may contain ".", so they must be sanitized before reaching a provider.
_WIRE_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_WIRE_MAX = 64


def mcp_tool_wire_name(
    server_id: str,
    tool_name: str,
    *,
    disambiguate: bool = False,
) -> str:
    """Derive a provider-safe function name for an MCP tool.

    MCP tools use the readable ``mcp_{tool}`` shape. A deterministic suffix is
    added when the name exceeds the provider limit or when the caller detects
    the same wire name on more than one server.
    """

    normalized_tool = _WIRE_UNSAFE.sub("_", tool_name)
    base = normalized_tool if normalized_tool.startswith("mcp_") else f"mcp_{normalized_tool}"
    if len(base) <= _WIRE_MAX and not disambiguate:
        return base
    digest = hashlib.sha256(f"{server_id}\0{tool_name}".encode()).hexdigest()[:8]
    return f"{base[: _WIRE_MAX - len(digest) - 1]}_{digest}"


def _suffix(value: str) -> str:
    normalized = value.strip().lower()
    try:
        return normalize_id(normalized)
    except ValueError:
        return hashlib.sha256(value.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class McpToolDefinition:
    stable_id: str
    server_id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]


@dataclass(frozen=True, slots=True)
class McpResourceDefinition:
    stable_id: str
    server_id: str
    uri: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class McpPromptDefinition:
    stable_id: str
    server_id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class McpCatalog:
    server_id: str
    instructions: str | None
    tools: tuple[McpToolDefinition, ...]
    resources: tuple[McpResourceDefinition, ...]
    prompts: tuple[McpPromptDefinition, ...]


def build_catalog(
    server_id: str,
    initialize: InitializeResult,
    tools: ListToolsResult,
    resources: ListResourcesResult,
    prompts: ListPromptsResult,
    resource_templates: ListResourceTemplatesResult | None = None,
    *,
    max_metadata_chars: int = 65_536,
) -> McpCatalog:
    server = normalize_id(server_id)
    instructions = initialize.instructions
    if instructions is not None and len(instructions) > max_metadata_chars:
        raise ValueError("MCP instructions exceed metadata limit")
    tool_items: list[McpToolDefinition] = []
    seen_tools: set[str] = set()
    for tool in tools.tools:
        name = normalize_id(tool.name)
        if name in seen_tools:
            raise ValueError(f"duplicate MCP tool: {name}")
        seen_tools.add(name)
        schema = tool.inputSchema
        annotations = {} if tool.annotations is None else tool.annotations.model_dump(mode="json")
        tool_items.append(
            McpToolDefinition(
                f"mcp:{server}/tool/{name}",
                server,
                tool.name,
                tool.description or "",
                schema,
                annotations,
            )
        )
    resource_items = [
        McpResourceDefinition(
            f"mcp:{server}/resource/{_suffix(str(resource.uri))}",
            server,
            str(resource.uri),
            resource.name,
            resource.description or "",
        )
        for resource in resources.resources
    ]
    if resource_templates is not None:
        resource_items.extend(
            McpResourceDefinition(
                f"mcp:{server}/resource/{_suffix(template.uriTemplate)}",
                server,
                template.uriTemplate,
                template.name,
                template.description or "",
            )
            for template in resource_templates.resourceTemplates
        )
    prompt_items = tuple(
        McpPromptDefinition(
            f"mcp:{server}/prompt/{normalize_id(prompt.name)}",
            server,
            prompt.name,
            prompt.description or "",
        )
        for prompt in prompts.prompts
    )
    return McpCatalog(
        server,
        instructions,
        tuple(sorted(tool_items, key=lambda item: item.stable_id)),
        tuple(sorted(resource_items, key=lambda item: item.stable_id)),
        tuple(sorted(prompt_items, key=lambda item: item.stable_id)),
    )
