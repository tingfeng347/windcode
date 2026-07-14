from __future__ import annotations

import copy
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import tomli_w
from pydantic import ValidationError

from windcode.config.models import AppConfig
from windcode.config.paths import default_user_config_path


class ConfigError(ValueError):
    def __init__(self, source: Path | str, message: str) -> None:
        self.source = source
        super().__init__(f"{source}: {message}")


def ensure_user_config(path: Path | None = None) -> Path:
    """Create the user config or add newly introduced defaults to an existing one."""
    target = (path or default_user_config_path()).expanduser().resolve()
    if target.exists():
        _ensure_user_extension_defaults(target)
        return target

    content = tomli_w.dumps(
        AppConfig().model_dump(mode="json", exclude_none=True, by_alias=True)
    ).encode()
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


def _ensure_user_extension_defaults(path: Path) -> None:
    """Add new user-level extension defaults without replacing explicit settings."""
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(path, str(exc)) from exc

    extensions_value = data.setdefault("extensions", {})
    if not isinstance(extensions_value, dict):
        return
    extensions = cast(dict[str, Any], extensions_value)
    changed = False
    if "enabled" not in extensions:
        extensions["enabled"] = True
        changed = True

    servers_value = extensions.setdefault("mcp_servers", {})
    if not isinstance(servers_value, dict):
        return
    servers = cast(dict[str, Any], servers_value)
    if "gaodemap-mcp" not in servers:
        servers["gaodemap-mcp"] = {
            "transport": "streamable_http",
            "enable": True,
            "url": "https://mcp.api-inference.modelscope.net/6eea030bc1684a/mcp",
            "required": True,
        }
        changed = True
    if not changed:
        return

    temporary = path.with_suffix(f"{path.suffix}.tmp-{uuid4().hex}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            tomli_w.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


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
    selected_user = user_file or default_user
    layers: list[tuple[Path | str, dict[str, Any], bool]] = [
        (selected_user, _read_toml(selected_user, required=user_file is not None), False),
    ]
    selected_project = project_file or default_project
    if selected_project.resolve() != selected_user.resolve():
        layers.append(
            (
                selected_project,
                _read_toml(selected_project, required=project_file is not None),
                True,
            )
        )
    if explicit_file is not None:
        layers.append((explicit_file, _read_toml(explicit_file, required=True), False))
    if overrides is not None:
        layers.append(("explicit overrides", dict(overrides), False))

    merged: dict[str, Any] = {}
    project_mcp_servers: set[str] = set()
    disabled_aliases: set[str] = set()
    last_source: Path | str = "built-in defaults"
    for source, layer, is_project_layer in layers:
        if layer:
            if is_project_layer:
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
