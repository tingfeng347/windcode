from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.tools.filesystem import content_sha256, require_workspace_path
from windcode.tools.write_file import WriteFileInput, WriteFileTool


class EditFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str
    expected_sha256: str | None = None


class EditFileTool:
    name = "edit_file"
    description = (
        "Replace one exact, unique text occurrence without overwriting concurrent changes."
    )
    input_model = EditFileInput
    effects = frozenset({ToolEffect.WORKSPACE_WRITE})

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(EditFileInput, arguments)
        path = require_workspace_path(context.workspace, parsed.path)
        content = path.read_text(encoding="utf-8")
        digest = content_sha256(content)
        if parsed.expected_sha256 is not None and parsed.expected_sha256 != digest:
            return ToolResult(
                output="file changed since it was read; expected digest does not match",
                is_error=True,
                data={"error": "stale_content", "actual_sha256": digest},
            )
        matches = content.count(parsed.old_text)
        if matches != 1:
            return ToolResult(
                output=f"old_text must match exactly once; found {matches} matches",
                is_error=True,
                data={"error": "non_unique_match", "matches": matches},
            )
        return await WriteFileTool().execute(
            context,
            WriteFileInput(
                path=parsed.path,
                content=content.replace(parsed.old_text, parsed.new_text, 1),
                expected_sha256=digest,
            ),
        )
