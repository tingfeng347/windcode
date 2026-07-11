from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.tools.filesystem import content_sha256, require_workspace_path


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=500, ge=1, le=2_000)


class ReadFileTool:
    name = "read_file"
    description = "Read a UTF-8 text file with stable one-based line numbers."
    input_model = ReadFileInput
    effects = frozenset({ToolEffect.READ})
    max_bytes = 2_000_000

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(ReadFileInput, arguments)
        path = require_workspace_path(context.workspace, parsed.path)
        raw = path.read_bytes()
        if len(raw) > self.max_bytes:
            return ToolResult(
                output=f"file is too large: {len(raw)} bytes (limit {self.max_bytes})",
                is_error=True,
                data={"error": "file_too_large", "size": len(raw)},
            )
        if b"\x00" in raw:
            return ToolResult(output="binary files are not supported", is_error=True)
        content = raw.decode("utf-8")
        lines = content.splitlines()
        start = parsed.offset - 1
        selected = lines[start : start + parsed.limit]
        output = "\n".join(
            f"{number:>6}\t{line}" for number, line in enumerate(selected, parsed.offset)
        )
        truncated = start + len(selected) < len(lines)
        return ToolResult(
            output=output,
            data={
                "path": str(path.relative_to(context.workspace.resolve())),
                "sha256": content_sha256(raw),
                "line_count": len(lines),
                "truncated": truncated,
            },
        )
