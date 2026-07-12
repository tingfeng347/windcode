from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from windcode.extensions.models import normalize_id
from windcode.extensions.paths import PathBoundaryError, read_bounded, resolve_beneath

MANIFEST_PATH = Path(".windcode-plugin/plugin.toml")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_ALLOWED = {
    "manifest_version",
    "id",
    "name",
    "version",
    "windcode",
    "required",
    "skills",
    "hooks",
    "commands",
    "mcp_servers",
    "permissions",
    "data",
}


@dataclass(frozen=True, slots=True)
class PluginComponent:
    component_id: str
    path: str


@dataclass(frozen=True, slots=True)
class PluginCommand:
    name: str
    target: str


@dataclass(frozen=True, slots=True)
class PluginManifest:
    manifest_version: int
    plugin_id: str
    name: str
    version: str
    windcode: str
    required: bool
    skills: tuple[PluginComponent, ...]
    hooks: tuple[PluginComponent, ...]
    mcp_servers: tuple[PluginComponent, ...]
    commands: tuple[PluginCommand, ...]
    effects: tuple[str, ...]
    network_hosts: tuple[str, ...]
    persistent_data: bool
    root: Path


def _components(root: Path, raw: object, label: str) -> tuple[PluginComponent, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be an array")
    result: list[PluginComponent] = []
    seen: set[str] = set()
    for value in cast(list[object], raw):
        if not isinstance(value, dict):
            raise ValueError(f"{label} entries must be tables")
        item = cast(dict[str, object], value)
        if set(item) != {"id", "path"}:
            raise ValueError(f"{label} entries require only id and path")
        component_id = normalize_id(str(item["id"]))
        if component_id in seen:
            raise ValueError(f"duplicate {label} id: {component_id}")
        path = str(item["path"])
        resolve_beneath(root, path)
        seen.add(component_id)
        result.append(PluginComponent(component_id, path))
    return tuple(sorted(result, key=lambda item: item.component_id))


def _mcp_components(root: Path, raw: object) -> tuple[PluginComponent, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("mcp_servers must be a table")
    return _components(
        root,
        [{"id": key, "path": value["path"]} for key, value in cast(dict[str, Any], raw).items()],
        "mcp_servers",
    )


def _commands(raw: object) -> tuple[PluginCommand, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("commands must be an array")
    result: list[PluginCommand] = []
    seen: set[str] = set()
    for value in cast(list[object], raw):
        if not isinstance(value, dict):
            raise ValueError("command entries must be tables")
        item = cast(dict[str, object], value)
        if set(item) != {"name", "target"}:
            raise ValueError("command entries require only name and target")
        name = normalize_id(str(item["name"]))
        target = str(item["target"])
        if name in seen or not re.fullmatch(
            r"(?:skill|prompt|capability):[a-z0-9][a-z0-9_.-]*", target
        ):
            raise ValueError(f"invalid or duplicate command: {name}")
        seen.add(name)
        result.append(PluginCommand(name, target))
    return tuple(sorted(result, key=lambda item: item.name))


def parse_plugin_manifest(root: Path, *, max_bytes: int = 65_536) -> PluginManifest:
    root = root.expanduser().resolve(strict=True)
    try:
        raw = cast(
            dict[str, object],
            tomllib.loads(read_bounded(root, MANIFEST_PATH, max_bytes=max_bytes).decode("utf-8")),
        )
    except (UnicodeError, tomllib.TOMLDecodeError, PathBoundaryError) as exc:
        raise ValueError(f"invalid plugin manifest: {exc}") from exc
    unknown = set(raw) - _ALLOWED
    if unknown:
        raise ValueError(f"unknown manifest fields: {', '.join(sorted(unknown))}")
    required_fields = {"manifest_version", "id", "name", "version", "windcode"}
    missing = required_fields - set(raw)
    if missing:
        raise ValueError(f"missing manifest fields: {', '.join(sorted(missing))}")
    if raw["manifest_version"] != 1:
        raise ValueError("unsupported manifest version")
    plugin_id = normalize_id(str(raw["id"]))
    name, version, compatibility = str(raw["name"]), str(raw["version"]), str(raw["windcode"])
    if not name.strip() or len(name) > 100 or not _VERSION.fullmatch(version):
        raise ValueError("invalid plugin name or version")
    if compatibility not in {">=0.1,<0.2", ">=0.1.0,<0.2.0", "*"}:
        raise ValueError(f"incompatible Windcode version range: {compatibility}")
    permissions = cast(dict[str, object], raw.get("permissions", {}))
    if set(permissions) - {"effects", "network_hosts"}:
        raise ValueError("unknown permissions fields")
    data = cast(dict[str, object], raw.get("data", {}))
    if set(data) - {"persistent"}:
        raise ValueError("unknown data fields")
    return PluginManifest(
        1,
        plugin_id,
        name.strip(),
        version,
        compatibility,
        bool(raw.get("required", False)),
        _components(root, raw.get("skills"), "skills"),
        _components(root, raw.get("hooks"), "hooks"),
        _mcp_components(root, raw.get("mcp_servers")),
        _commands(raw.get("commands")),
        tuple(sorted(str(value) for value in cast(list[object], permissions.get("effects", [])))),
        tuple(
            sorted(str(value) for value in cast(list[object], permissions.get("network_hosts", [])))
        ),
        bool(data.get("persistent", False)),
        root,
    )
