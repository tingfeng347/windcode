from __future__ import annotations

import copy
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from platformdirs import user_config_path
from pydantic import ValidationError

from windcode.config.models import AppConfig


class ConfigError(ValueError):
    def __init__(self, source: Path | str, message: str) -> None:
        self.source = source
        super().__init__(f"{source}: {message}")


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
    default_user = user_config_path("windcode") / "config.toml"
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
    last_source: Path | str = "built-in defaults"
    for source, layer in layers:
        if layer:
            merged = _deep_merge(merged, layer)
            last_source = source
    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(last_source, str(exc)) from exc
