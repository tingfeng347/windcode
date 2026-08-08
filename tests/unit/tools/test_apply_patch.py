from pathlib import Path

import pytest

from windcode.domain.tools import ToolContext
from windcode.tools.apply_patch import (
    ApplyPatchInput,
    ApplyPatchTool,
    PatchParseError,
    parse_unified_diff,
)


@pytest.mark.asyncio
async def test_applies_multiple_files(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("old\n")
    patch = """--- a/one.txt
+++ b/one.txt
@@ -1 +1 @@
-old
+new
--- /dev/null
+++ b/two.txt
@@ -0,0 +1 @@
+created
"""
    result = await ApplyPatchTool().execute(
        ToolContext(tmp_path, "run", lambda: False), ApplyPatchInput(patch=patch)
    )

    assert not result.is_error
    assert (tmp_path / "one.txt").read_text() == "new\n"
    assert (tmp_path / "two.txt").read_text() == "created\n"


@pytest.mark.asyncio
async def test_preflight_conflict_leaves_all_files_unchanged(tmp_path: Path) -> None:
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    one.write_text("current\n")
    two.write_text("second\n")
    patch = """--- a/one.txt
+++ b/one.txt
@@ -1 +1 @@
-current
+changed
--- a/two.txt
+++ b/two.txt
@@ -1 +1 @@
-wrong
+changed
"""

    with pytest.raises(ValueError, match="does not match"):
        await ApplyPatchTool().execute(
            ToolContext(tmp_path, "run", lambda: False), ApplyPatchInput(patch=patch)
        )
    assert one.read_text() == "current\n"
    assert two.read_text() == "second\n"


def test_rejects_unsafe_and_binary_patches() -> None:
    with pytest.raises(PatchParseError, match="unsafe"):
        parse_unified_diff("--- a/../x\n+++ b/../x\n@@ -0,0 +1 @@\n+x\n")
    with pytest.raises(PatchParseError, match="binary"):
        parse_unified_diff("GIT binary patch\n")
