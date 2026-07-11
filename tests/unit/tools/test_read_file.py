from pathlib import Path

import pytest

from windcode.domain.tools import ToolContext
from windcode.tools.read_file import ReadFileInput, ReadFileTool


@pytest.mark.asyncio
async def test_reads_numbered_range_and_digest(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("one\ntwo\nthree\n")
    result = await ReadFileTool().execute(
        ToolContext(tmp_path, "run", lambda: False),
        ReadFileInput(path="file.txt", offset=2, limit=1),
    )

    assert "2\ttwo" in result.output
    assert result.data["truncated"] is True
    assert len(result.data["sha256"]) == 64


@pytest.mark.asyncio
async def test_rejects_binary_file(tmp_path: Path) -> None:
    (tmp_path / "binary").write_bytes(b"a\x00b")
    result = await ReadFileTool().execute(
        ToolContext(tmp_path, "run", lambda: False), ReadFileInput(path="binary")
    )
    assert result.is_error
