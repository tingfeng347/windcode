from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from pydantic import BaseModel, ConfigDict

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.tools import ToolRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class EchoTool:
    name = "echo"
    description = "Echo text."
    input_model = EchoInput
    effects = frozenset({ToolEffect.READ})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        del context
        return ToolResult(cast(EchoInput, arguments).text)


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(tmp_path, "run", lambda: False)


@pytest.mark.asyncio
async def test_registers_schema_and_validates_arguments(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    assert registry.schemas()[0].parameters["required"] == ["text"]
    result = await registry.execute("echo", context(tmp_path), {"text": "hello"})
    assert result.output == "hello"
    assert result.elapsed_seconds > 0
    invalid = await registry.execute("echo", context(tmp_path), {"unknown": True})
    assert invalid.is_error
    assert invalid.data["error"] == "invalid_arguments"


def test_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


def test_clone_preserves_order_and_isolates_replacements() -> None:
    registry = ToolRegistry()
    original = EchoTool()
    registry.register(original)

    cloned = registry.clone()
    replacement = EchoTool()
    cloned.register(replacement, replace=True)

    assert registry.names() == cloned.names() == ("echo",)
    assert registry.get("echo") is original
    assert cloned.get("echo") is replacement


class RawSchemaTool(EchoTool):
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


@pytest.mark.asyncio
async def test_raw_json_schema_is_preserved_and_validated(tmp_path: Path) -> None:
    registry = ToolRegistry()
    tool = RawSchemaTool()
    registry.register(tool)

    assert registry.schemas()[0].parameters is not tool.input_schema
    assert registry.schemas()[0].parameters == tool.input_schema
    invalid = await registry.execute("echo", context(tmp_path), {"items": [True]})
    assert invalid.is_error
    assert invalid.data["error"] == "invalid_arguments"
    assert "items" in invalid.output
