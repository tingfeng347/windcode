from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    requested: Path
    path: Path
    workspace: Path
    inside_workspace: bool
    symlink_escape: bool


def resolve_path(workspace: Path, requested: str | Path) -> ResolvedPath:
    root = workspace.expanduser().resolve()
    raw = Path(requested).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    lexical_inside = Path(os.path.abspath(candidate)).is_relative_to(root)
    resolved = candidate.resolve(strict=False)
    inside = resolved.is_relative_to(root)
    return ResolvedPath(
        requested=raw,
        path=resolved,
        workspace=root,
        inside_workspace=inside,
        symlink_escape=lexical_inside and not inside,
    )


def require_workspace_path(workspace: Path, requested: str | Path) -> Path:
    resolved = resolve_path(workspace, requested)
    if not resolved.inside_workspace:
        reason = (
            "symbolic link escapes workspace"
            if resolved.symlink_escape
            else "path is outside workspace"
        )
        raise ValueError(f"{requested}: {reason}")
    return resolved.path


def content_sha256(content: bytes | str) -> str:
    encoded = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    return content_sha256(path.read_bytes())


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode: int | None = None
    if path.exists():
        previous_mode = stat.S_IMODE(path.stat().st_mode)
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if previous_mode is not None:
            temporary.chmod(previous_mode)
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
