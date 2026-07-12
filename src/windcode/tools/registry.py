from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from time import monotonic
from typing import Any, Protocol, cast

import jsonschema
from pydantic import ValidationError

from windcode.domain.models import ToolSchema
from windcode.domain.tools import Tool, ToolContext, ToolResult, ValidatedArguments


class _RawSchemaTool(Protocol):
    @property
    def input_schema(self) -> Mapping[str, Any]: ...

    def validate_arguments(self, arguments: Mapping[str, Any]) -> ValidatedArguments: ...


def _input_schema(tool: Tool) -> dict[str, Any]:
    raw = getattr(tool, "input_schema", None)
    if isinstance(raw, Mapping):
        mapping = cast(Mapping[object, object], raw)
        return {str(key): value for key, value in mapping.items()}
    return tool.input_model.model_json_schema()


def _validate(tool: Tool, arguments: Mapping[str, Any]) -> ValidatedArguments:
    validator = getattr(tool, "validate_arguments", None)
    if callable(validator):
        return cast(_RawSchemaTool, tool).validate_arguments(arguments)
    schema = getattr(tool, "input_schema", None)
    if isinstance(schema, Mapping):
        typed_schema = cast(Mapping[str, Any], schema)
        validator = jsonschema.Draft202012Validator(typed_schema)
        validator.validate(arguments)  # pyright: ignore[reportUnknownMemberType]
        return dict(arguments)
    return tool.input_model.model_validate(arguments)


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
                parameters=_input_schema(tool),
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
            parsed = _validate(tool, arguments)
        except (ValidationError, jsonschema.ValidationError, jsonschema.SchemaError) as exc:
            errors: object
            if isinstance(exc, ValidationError):
                errors = exc.errors(include_url=False)
            elif isinstance(exc, jsonschema.ValidationError):
                path_parts = cast(tuple[object, ...], tuple(exc.absolute_path))
                path_values: list[str] = [str(part) for part in path_parts]
                errors = cast(
                    object,
                    [{"path": path_values, "message": str(exc.message)}],
                )
            else:
                errors = [{"path": tuple[str, ...](), "message": str(exc)}]
            return ToolResult(
                output=json.dumps(errors, ensure_ascii=True),
                is_error=True,
                data={"error": "invalid_arguments"},
            )
        started = monotonic()
        try:
            result = await tool.execute(context, cast(Any, parsed))
        except (OSError, ValueError, UnicodeError) as exc:
            result = ToolResult(
                output=str(exc),
                is_error=True,
                data={"error": "execution_failed", "type": type(exc).__name__},
            )
        return replace(result, elapsed_seconds=max(0.0, monotonic() - started))
