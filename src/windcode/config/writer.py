from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import tomli_w

from windcode.config.models import AppConfig


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

    data["providers"] = {
        alias: provider.model_dump(mode="json", exclude_none=True)
        for alias, provider in updated.providers.items()
    }
    if updated.primary_provider is None:
        data.pop("primary_provider", None)
    else:
        data["primary_provider"] = updated.primary_provider
    data["fallback_chain"] = list(updated.fallback_chain)
    data["enabled_providers"] = sorted(updated.providers)
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
