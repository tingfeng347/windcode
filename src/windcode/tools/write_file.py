from __future__ import annotations

import difflib
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.tools.filesystem import (
    atomic_write_text,
    content_sha256,
    file_sha256,
    require_workspace_path,
)


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str
    expected_sha256: str | None = None


class WriteFileTool:
    name = "write_file"
    description = "Create or atomically replace a UTF-8 file, optionally checking its prior digest."
    input_model = WriteFileInput
    effects = frozenset({ToolEffect.WORKSPACE_WRITE})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(WriteFileInput, arguments)
        path = require_workspace_path(context.workspace, parsed.path)
        existed = path.exists()
        old_content = path.read_text(encoding="utf-8") if existed else ""
        old_digest = content_sha256(old_content) if existed else None
        if parsed.expected_sha256 is not None and parsed.expected_sha256 != old_digest:
            return ToolResult(
                output="file changed since it was read; expected digest does not match",
                is_error=True,
                data={"error": "stale_content", "actual_sha256": old_digest},
            )
        atomic_write_text(path, parsed.content)
        relative = str(path.relative_to(context.workspace.resolve()))
        diff = "".join(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                parsed.content.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        return ToolResult(
            output=diff or "file content unchanged",
            data={
                "path": relative,
                "action": "modified" if existed else "created",
                "before_sha256": old_digest,
                "after_sha256": file_sha256(path),
            },
        )
