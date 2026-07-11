from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, cast

REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "authorization",
    "proxy_authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "credential",
    "credentials",
}


def _normalize_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalize_key(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        ("_api_key", "_access_token", "_refresh_token", "_password", "_secret")
    )


def _redact_string(value: str, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    return redacted


def redact(value: Any, *, secrets: Iterable[str] = ()) -> Any:
    """Return a recursively redacted copy without mutating the input."""
    secret_values = tuple(secret for secret in secrets if secret)

    def visit(item: object) -> object:
        if isinstance(item, Mapping):
            mapping = cast(Mapping[object, object], item)
            return {
                str(key): REDACTED if _is_sensitive_key(key) else visit(child)
                for key, child in mapping.items()
            }
        if isinstance(item, tuple):
            sequence = cast(tuple[object, ...], item)
            return tuple(visit(child) for child in sequence)
        if isinstance(item, list):
            sequence = cast(list[object], item)
            return [visit(child) for child in sequence]
        if isinstance(item, set):
            sequence = cast(set[object], item)
            return {visit(child) for child in sequence}
        if isinstance(item, str):
            return _redact_string(item, secret_values)
        return item

    return visit(cast(object, value))
