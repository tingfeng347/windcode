from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from windcode._fsync import fsync_directory
from windcode.extensions.paths import PathBoundaryError, scan_bounded
from windcode.extensions.plugins.manifest import PluginManifest, parse_plugin_manifest

_IGNORED_PARTS = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache"}


@dataclass(frozen=True, slots=True)
class InstallResult:
    manifest: PluginManifest
    digest: str
    destination: Path
    changed: bool


def _plugin_files(root: Path, *, max_entries: int = 10_000) -> tuple[Path, ...]:
    files = (
        path
        for path in scan_bounded(root, max_depth=32, max_entries=max_entries)
        if not any(part in _IGNORED_PARTS for part in path.relative_to(root).parts)
    )
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def plugin_digest(root: Path) -> str:
    root = root.expanduser().resolve(strict=True)
    digest = hashlib.sha256()
    for path in _plugin_files(root):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def install_local_plugin(source: Path, plugins_root: Path) -> InstallResult:
    source = source.expanduser().resolve(strict=True)
    manifest = parse_plugin_manifest(source)
    digest = plugin_digest(source)
    plugin_root = plugins_root.expanduser().resolve() / manifest.plugin_id
    destination = plugin_root / digest
    if destination.exists():
        return InstallResult(manifest, digest, destination, False)
    if plugin_root.exists():
        for installed in plugin_root.iterdir():
            if not installed.is_dir():
                continue
            try:
                existing = parse_plugin_manifest(installed)
            except ValueError:
                continue
            if existing.version == manifest.version and installed.name != digest:
                raise ValueError(
                    f"plugin {manifest.plugin_id} version {manifest.version} has different content"
                )
    plugin_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = plugin_root / f".tmp-{uuid4().hex}"
    temporary.mkdir(mode=0o700)
    try:
        for path in _plugin_files(source):
            relative = path.relative_to(source)
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, target, follow_symlinks=False)
        if plugin_digest(temporary) != digest:
            raise PathBoundaryError("plugin content changed during installation")
        parse_plugin_manifest(temporary)
        for directory, _, files in os.walk(temporary):
            for name in files:
                descriptor = os.open(Path(directory) / name, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        os.replace(temporary, destination)
        fsync_directory(plugin_root)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return InstallResult(manifest, digest, destination, True)
