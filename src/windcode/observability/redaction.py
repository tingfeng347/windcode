from __future__ import annotations

import re
import threading
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


class DynamicRedactor:
    """Run-scoped sensitive value registry with explicit cleanup."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: str) -> None:
        if not secret:
            return
        with self._lock:
            self._secrets.add(secret)

    def redact(self, value: Any) -> Any:
        with self._lock:
            secrets = tuple(sorted(self._secrets, key=len, reverse=True))
        return redact(value, secrets=secrets)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def __enter__(self) -> DynamicRedactor:
        return self

    def __exit__(self, *_: object) -> None:
        self.clear()
