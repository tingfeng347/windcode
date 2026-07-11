from __future__ import annotations

import re
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult


class GrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1)
    glob: str = "**/*"
    context_lines: int = Field(default=0, ge=0, le=10)
    limit: int = Field(default=200, ge=1, le=2_000)
    case_sensitive: bool = True


class GrepTool:
    name = "grep"
    description = "Search UTF-8 workspace text with a regular expression and bounded context."
    input_model = GrepInput
    effects = frozenset({ToolEffect.READ})
    max_file_bytes = 2_000_000

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(GrepInput, arguments)
        flags = 0 if parsed.case_sensitive else re.IGNORECASE
        expression = re.compile(parsed.pattern, flags)
        root = context.workspace.resolve()
        output: list[str] = []
        matched = 0
        for path in sorted(root.glob(parsed.glob)):
            resolved = path.resolve()
            if not path.is_file() or not resolved.is_relative_to(root):
                continue
            raw = path.read_bytes()
            if len(raw) > self.max_file_bytes or b"\x00" in raw:
                continue
            try:
                lines = raw.decode("utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            relative = resolved.relative_to(root)
            for index, line in enumerate(lines):
                if expression.search(line) is None:
                    continue
                matched += 1
                start = max(0, index - parsed.context_lines)
                end = min(len(lines), index + parsed.context_lines + 1)
                for line_index in range(start, end):
                    separator = ":" if line_index == index else "-"
                    output.append(
                        f"{relative}{separator}{line_index + 1}{separator}{lines[line_index]}"
                    )
                if matched >= parsed.limit:
                    return ToolResult(
                        output="\n".join(output),
                        data={"matches": matched, "truncated": True},
                    )
        return ToolResult(
            output="\n".join(output),
            data={"matches": matched, "truncated": False},
        )
