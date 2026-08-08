from pathlib import Path

import pytest

from windcode.tools.filesystem import (
    atomic_write_text,
    file_sha256,
    require_workspace_path,
    resolve_path,
)


def test_resolves_workspace_path_and_rejects_escape(tmp_path: Path) -> None:
    assert require_workspace_path(tmp_path, "src/file.py") == tmp_path / "src" / "file.py"
    with pytest.raises(ValueError, match="outside workspace"):
        require_workspace_path(tmp_path, "../outside")


def test_detects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    resolved = resolve_path(tmp_path, "link/file")

    assert resolved.symlink_escape
    with pytest.raises(ValueError, match="symbolic link"):
        require_workspace_path(tmp_path, "link/file")


def test_atomic_write_and_digest(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "file.txt"
    atomic_write_text(path, "content")
    assert path.read_text() == "content"
    assert len(file_sha256(path)) == 64
