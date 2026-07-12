from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path


class PathBoundaryError(ValueError):
    pass


def resolve_beneath(root: Path, relative: str | Path, *, require_file: bool = False) -> Path:
    root = root.expanduser().resolve(strict=True)
    candidate_part = Path(relative)
    if candidate_part.is_absolute() or ".." in candidate_part.parts:
        raise PathBoundaryError(f"path escapes extension root: {relative}")
    candidate = root.joinpath(candidate_part).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise PathBoundaryError(f"path escapes extension root: {relative}")
    mode = candidate.stat().st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise PathBoundaryError(f"unsupported extension file type: {relative}")
    if require_file and not stat.S_ISREG(mode):
        raise PathBoundaryError(f"extension path is not a regular file: {relative}")
    return candidate


def read_bounded(root: Path, relative: str | Path, *, max_bytes: int) -> bytes:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    path = resolve_beneath(root, relative, require_file=True)
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PathBoundaryError(f"extension file exceeds {max_bytes} bytes: {relative}")
    return data


def scan_bounded(root: Path, *, max_depth: int, max_entries: int) -> Iterator[Path]:
    if max_depth < 0 or max_entries < 1:
        raise ValueError("invalid scan bounds")
    resolved_root = root.expanduser().resolve(strict=True)
    seen = 0
    pending: list[tuple[Path, int]] = [(resolved_root, 0)]
    while pending:
        directory, depth = pending.pop()
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        child_directories: list[Path] = []
        for entry in ordered:
            seen += 1
            if seen > max_entries:
                raise PathBoundaryError(f"extension scan exceeds {max_entries} entries")
            if entry.is_symlink():
                continue
            path = Path(entry.path)
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISREG(mode):
                yield path
            elif stat.S_ISDIR(mode) and depth < max_depth:
                child_directories.append(path)
            elif not stat.S_ISDIR(mode):
                raise PathBoundaryError(f"unsupported extension file type: {path}")
        pending.extend((path, depth + 1) for path in reversed(child_directories))


def plugin_data_directory(data_root: Path, plugin_id: str) -> Path:
    from windcode.extensions.models import normalize_id

    return data_root.expanduser().resolve() / "plugin-data" / normalize_id(plugin_id)
