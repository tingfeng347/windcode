from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import tomli_w

from windcode.config.models import AppConfig, ExtensionConfig


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    return value


def save_model_config(path: Path, previous: AppConfig, updated: AppConfig) -> None:
    """Persist model profiles without storing API key values."""
    path = path.expanduser().resolve()
    data = _read_config(path)
    raw_disabled = data.get("disabled_providers", [])
    disabled: set[str] = set()
    if isinstance(raw_disabled, list):
        disabled = {str(alias) for alias in cast(list[object], raw_disabled)}
    disabled.update(previous.providers.keys() - updated.providers.keys())
    disabled.difference_update(updated.providers)

    file_providers: dict[str, Any] = {}
    raw_providers = data.get("providers", {})
    if isinstance(raw_providers, dict):
        file_providers = cast(dict[str, Any], raw_providers)
    file_aliases: set[str] = set(file_providers)
    previous_aliases: set[str] = set(previous.providers.keys())
    updated_aliases: set[str] = set(updated.providers.keys())
    changed_aliases = {
        alias
        for alias in previous_aliases & updated_aliases
        if previous.providers[alias] != updated.providers[alias]
    }
    user_added = updated_aliases - previous_aliases
    user_removed = previous_aliases - updated_aliases

    merged: dict[str, Any] = {alias: file_providers[alias] for alias in file_aliases - user_removed}
    for alias in sorted((file_aliases | user_added | changed_aliases) & updated_aliases):
        merged[alias] = updated.providers[alias].model_dump(mode="json", exclude_none=True)
    data["providers"] = merged

    if updated.primary_provider is None:
        data.pop("primary_provider", None)
    else:
        data["primary_provider"] = updated.primary_provider
    data["fallback_chain"] = list(updated.fallback_chain)
    data["enabled_providers"] = sorted(merged)
    if disabled:
        data["disabled_providers"] = sorted(disabled)
    else:
        data.pop("disabled_providers", None)

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{uuid4().hex}")
    try:
        with temporary.open("wb") as stream:
            tomli_w.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_memory_config(path: Path, config: AppConfig) -> None:
    """Persist non-secret memory policy without rewriting unrelated configuration."""
    path = path.expanduser().resolve()
    data = _read_config(path)
    data["memory"] = config.memory.model_dump(mode="json", exclude_none=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{uuid4().hex}")
    try:
        with temporary.open("wb") as stream:
            tomli_w.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_extension_config(path: Path, extensions: ExtensionConfig) -> None:
    """Persist extension discovery and MCP configuration atomically."""
    path = path.expanduser().resolve()
    data = _read_config(path)
    data["extensions"] = extensions.model_dump(
        mode="json",
        by_alias=True,
        exclude={"project_mcp_servers"},
        exclude_none=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{uuid4().hex}")
    try:
        with temporary.open("wb") as stream:
            tomli_w.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
