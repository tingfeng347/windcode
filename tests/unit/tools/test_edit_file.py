from pathlib import Path

import pytest

from windcode.domain.tools import ToolContext
from windcode.tools.edit_file import EditFileInput, EditFileTool


@pytest.mark.asyncio
async def test_replaces_unique_text(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_text("before value after")
    result = await EditFileTool().execute(
        ToolContext(tmp_path, "run", lambda: False),
        EditFileInput(path="file", old_text="value", new_text="changed"),
    )
    assert not result.is_error
    assert path.read_text() == "before changed after"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["no match", "x x"])
async def test_non_unique_match_has_no_side_effect(tmp_path: Path, content: str) -> None:
    path = tmp_path / "file"
    path.write_text(content)
    result = await EditFileTool().execute(
        ToolContext(tmp_path, "run", lambda: False),
        EditFileInput(path="file", old_text="x", new_text="y"),
    )
    assert result.is_error
    assert path.read_text() == content
