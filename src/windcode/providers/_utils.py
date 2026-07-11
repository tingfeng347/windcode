from __future__ import annotations

from collections.abc import Mapping
from typing import cast


def get_value(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return mapping.get(name, default)
    return getattr(value, name, default)


def as_string(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def as_int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
