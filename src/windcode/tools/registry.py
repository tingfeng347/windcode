from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from windcode.domain.models import ToolSchema
from windcode.domain.tools import Tool, ToolContext, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        if tool.name in self._tools and not replace:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def clone(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry._tools = self._tools.copy()
        return registry

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(
                name=tool.name,
                description=tool.description,
                parameters=tool.input_model.model_json_schema(),
            )
            for tool in self._tools.values()
        )

    async def execute(
        self,
        name: str,
        context: ToolContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        try:
            tool = self.get(name)
        except KeyError as exc:
            return ToolResult(
                output=str(exc),
                is_error=True,
                data={"error": "unknown_tool", "tool": name},
            )
        try:
            parsed = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(
                output=json.dumps(exc.errors(include_url=False), ensure_ascii=True),
                is_error=True,
                data={"error": "invalid_arguments"},
            )
        try:
            return await tool.execute(context, parsed)
        except (OSError, ValueError, UnicodeError) as exc:
            return ToolResult(
                output=str(exc),
                is_error=True,
                data={"error": "execution_failed", "type": type(exc).__name__},
            )
