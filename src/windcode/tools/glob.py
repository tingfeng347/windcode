from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult


class GlobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1)
    limit: int = Field(default=200, ge=1, le=2_000)


class GlobTool:
    name = "glob"
    description = "Match workspace files using a glob pattern and return stable sorted paths."
    input_model = GlobInput
    effects = frozenset({ToolEffect.READ})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(GlobInput, arguments)
        root = context.workspace.resolve()
        matches = sorted(
            str(path.resolve().relative_to(root))
            for path in root.glob(parsed.pattern)
            if path.is_file() and path.resolve().is_relative_to(root)
        )
        selected = matches[: parsed.limit]
        return ToolResult(
            output="\n".join(selected),
            data={"count": len(selected), "truncated": len(matches) > len(selected)},
        )
