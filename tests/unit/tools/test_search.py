from pathlib import Path

import pytest

from windcode.domain.tools import ToolContext
from windcode.tools.glob import GlobInput, GlobTool
from windcode.tools.grep import GrepInput, GrepTool


@pytest.mark.asyncio
async def test_glob_is_sorted_and_bounded(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("")
    (tmp_path / "a.py").write_text("")
    result = await GlobTool().execute(
        ToolContext(tmp_path, "run", lambda: False), GlobInput(pattern="*.py", limit=1)
    )
    assert result.output == "a.py"
    assert result.data["truncated"] is True


@pytest.mark.asyncio
async def test_grep_skips_binary_and_returns_context(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("before\nNeedle\nafter\n")
    (tmp_path / "binary").write_bytes(b"Needle\x00")
    result = await GrepTool().execute(
        ToolContext(tmp_path, "run", lambda: False),
        GrepInput(pattern="needle", case_sensitive=False, context_lines=1),
    )
    assert "a.txt:2:Needle" in result.output
    assert "a.txt-1-before" in result.output
    assert "binary" not in result.output
