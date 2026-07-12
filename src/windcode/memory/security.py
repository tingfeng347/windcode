from __future__ import annotations

import re

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|api[_ -]?key|access[_ -]?token|secret)\b\s*[:=]"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
)


class SensitiveMemoryError(ValueError):
    pass


def contains_sensitive_data(text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in _SENSITIVE_PATTERNS)


def validate_memory_text(*values: str) -> None:
    if any(contains_sensitive_data(value) for value in values):
        raise SensitiveMemoryError("memory contains credentials or sensitive secret material")
