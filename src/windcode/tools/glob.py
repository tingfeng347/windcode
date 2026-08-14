from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult


def _workspace_pattern(root: Path, pattern: str) -> str | None:
    raw_pattern = Path(pattern).expanduser()
    if not raw_pattern.anchor:
        return pattern
    if not raw_pattern.is_absolute():
        return None
    try:
        return str(raw_pattern.relative_to(root))
    except ValueError:
        return None


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
        pattern = _workspace_pattern(root, parsed.pattern)
        if pattern is None:
            return ToolResult(
                "absolute glob pattern is outside the assigned workspace; use a relative pattern",
                is_error=True,
                data={"error": "pattern_outside_workspace"},
            )
        matches, selected = await asyncio.to_thread(self._collect, root, pattern, parsed.limit)
        return ToolResult(
            output="\n".join(selected),
            data={"count": len(selected), "truncated": len(matches) > len(selected)},
        )

    @staticmethod
    def _collect(root: Path, pattern: str, limit: int) -> tuple[list[str], list[str]]:
        matches = sorted(
            str(path.resolve().relative_to(root))
            for path in root.glob(pattern)
            if path.is_file() and path.resolve().is_relative_to(root)
        )
        return matches, matches[:limit]
