from pathlib import Path

import pytest

from windcode.instructions import load_instructions


def test_loads_root_to_current_and_uses_same_level_priority(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("root")
    (tmp_path / "HERMES.md").write_text("ignored")
    (tmp_path / "src" / "WINDCODE.md").write_text("src")
    (nested / "AGENTS.md").write_text("feature")
    (nested / "CLAUDE.md").write_text("ignored")

    blocks = load_instructions(nested, workspace_root=tmp_path)

    assert [block.content for block in blocks] == ["root", "src", "feature"]
    assert [block.path.name for block in blocks] == ["CLAUDE.md", "WINDCODE.md", "AGENTS.md"]


def test_rejects_current_directory_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside workspace"):
        load_instructions(outside, workspace_root=workspace)
