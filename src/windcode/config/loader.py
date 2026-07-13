from __future__ import annotations

import copy
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import tomli_w
from platformdirs import user_config_path
from pydantic import ValidationError

from windcode.config.models import AppConfig


class ConfigError(ValueError):
    def __init__(self, source: Path | str, message: str) -> None:
        self.source = source
        super().__init__(f"{source}: {message}")


def default_user_config_path() -> Path:
    return user_config_path("windcode") / "config.toml"


def ensure_user_config(path: Path | None = None) -> Path:
    """Create the default user configuration once without overwriting an existing file."""
    target = (path or default_user_config_path()).expanduser().resolve()
    if target.exists():
        return target

    content = tomli_w.dumps(AppConfig().model_dump(mode="json", exclude_none=True)).encode()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return target

    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return target


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(
                cast(dict[str, Any], previous), cast(Mapping[str, Any], value)
            )
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read_toml(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ConfigError(path, "configuration file does not exist")
        return {}
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(path, str(exc)) from exc


def load_config(
    workspace: Path,
    *,
    user_file: Path | None = None,
    project_file: Path | None = None,
    explicit_file: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    workspace = workspace.expanduser().resolve()
    default_user = default_user_config_path()
    default_project = workspace / ".windcode" / "config.toml"
    layers: list[tuple[Path | str, dict[str, Any]]] = [
        (
            user_file or default_user,
            _read_toml(user_file or default_user, required=user_file is not None),
        ),
        (
            project_file or default_project,
            _read_toml(project_file or default_project, required=project_file is not None),
        ),
    ]
    if explicit_file is not None:
        layers.append((explicit_file, _read_toml(explicit_file, required=True)))
    if overrides is not None:
        layers.append(("explicit overrides", dict(overrides)))

    merged: dict[str, Any] = {}
    project_mcp_servers: set[str] = set()
    disabled_aliases: set[str] = set()
    last_source: Path | str = "built-in defaults"
    for layer_index, (source, layer) in enumerate(layers):
        if layer:
            if layer_index == 1:
                raw_extensions = layer.get("extensions")
                if isinstance(raw_extensions, Mapping):
                    extension_values = cast(Mapping[object, object], raw_extensions)
                    raw_mcp = extension_values.get("mcp_servers")
                    if isinstance(raw_mcp, Mapping):
                        mcp_values = cast(Mapping[object, object], raw_mcp)
                        project_mcp_servers.update(str(server_id) for server_id in mcp_values)
            raw_disabled = layer.get("disabled_providers", [])
            raw_enabled = layer.get("enabled_providers", [])
            if not isinstance(raw_disabled, list) or not isinstance(raw_enabled, list):
                raise ConfigError(source, "provider enable/disable lists must be arrays")
            disabled_aliases.update(str(alias) for alias in cast(list[object], raw_disabled))
            disabled_aliases.difference_update(
                str(alias) for alias in cast(list[object], raw_enabled)
            )
            merged = _deep_merge(merged, layer)
            last_source = source
    merged.pop("disabled_providers", None)
    merged.pop("enabled_providers", None)
    if project_mcp_servers:
        raw_extensions = merged.setdefault("extensions", {})
        if isinstance(raw_extensions, dict):
            raw_extensions["project_mcp_servers"] = sorted(project_mcp_servers)
    if disabled_aliases and isinstance(merged.get("providers"), dict):
        providers = cast(dict[str, Any], merged["providers"])
        for alias in disabled_aliases:
            providers.pop(alias, None)
        if merged.get("primary_provider") in disabled_aliases:
            merged.pop("primary_provider", None)
        fallback = merged.get("fallback_chain")
        if isinstance(fallback, list):
            fallback_values = cast(list[object], fallback)
            merged["fallback_chain"] = [
                alias for alias in fallback_values if str(alias) not in disabled_aliases
            ]
    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(last_source, str(exc)) from exc
