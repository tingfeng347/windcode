from pathlib import Path

import pytest

from windcode.domain.tools import ToolContext
from windcode.tools.filesystem import content_sha256
from windcode.tools.write_file import WriteFileInput, WriteFileTool


@pytest.mark.asyncio
async def test_creates_and_modifies_file(tmp_path: Path) -> None:
    tool = WriteFileTool()
    context = ToolContext(tmp_path, "run", lambda: False)
    created = await tool.execute(context, WriteFileInput(path="file", content="one\n"))
    modified = await tool.execute(
        context,
        WriteFileInput(path="file", content="two\n", expected_sha256=content_sha256("one\n")),
    )

    assert created.data["action"] == "created"
    assert modified.data["action"] == "modified"
    assert (tmp_path / "file").read_text() == "two\n"


@pytest.mark.asyncio
async def test_stale_digest_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_text("current")
    result = await WriteFileTool().execute(
        ToolContext(tmp_path, "run", lambda: False),
        WriteFileInput(path="file", content="new", expected_sha256="stale"),
    )
    assert result.is_error
    assert path.read_text() == "current"
