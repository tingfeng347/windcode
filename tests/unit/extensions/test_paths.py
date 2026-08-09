import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from windcode.extensions.paths import (
    PathBoundaryError,
    plugin_data_directory,
    read_bounded,
    resolve_beneath,
    scan_bounded,
)

mkfifo = cast(Callable[[Path], None] | None, getattr(os, "mkfifo", None))


def test_resolve_beneath_accepts_regular_nested_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    path = root / "nested" / "file.txt"
    path.parent.mkdir(parents=True)
    path.write_text("content")

    assert resolve_beneath(root, "nested/file.txt", require_file=True) == path
    assert read_bounded(root, "nested/file.txt", max_bytes=7) == b"content"


@pytest.mark.parametrize("relative", ["../outside", "/etc/passwd"])
def test_resolve_beneath_rejects_lexical_escape(tmp_path: Path, relative: str) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(PathBoundaryError):
        resolve_beneath(root, relative)


def test_resolve_beneath_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.write_text("secret")
    (root / "link").symlink_to(outside)

    with pytest.raises(PathBoundaryError):
        resolve_beneath(root, "link", require_file=True)


def test_read_bounded_rejects_large_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "large").write_bytes(b"12345")

    with pytest.raises(PathBoundaryError):
        read_bounded(root, "large", max_bytes=4)


def test_scan_is_sorted_bounded_and_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "b").write_text("b")
    (root / "a").write_text("a")
    (root / "loop").symlink_to(root, target_is_directory=True)

    assert [path.name for path in scan_bounded(root, max_depth=2, max_entries=3)] == [
        "a",
        "b",
    ]
    with pytest.raises(PathBoundaryError):
        list(scan_bounded(root, max_depth=2, max_entries=2))


@pytest.mark.skipif(mkfifo is None, reason="FIFO is not supported")
def test_scan_rejects_special_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert mkfifo is not None
    mkfifo(root / "pipe")

    with pytest.raises(PathBoundaryError):
        list(scan_bounded(root, max_depth=1, max_entries=2))


def test_plugin_data_directory_is_host_managed(tmp_path: Path) -> None:
    assert plugin_data_directory(tmp_path, "Example") == tmp_path / "plugin-data" / "example"
    with pytest.raises(ValueError):
        plugin_data_directory(tmp_path, "../escape")
