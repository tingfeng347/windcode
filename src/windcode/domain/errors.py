from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    SERVER = "server"
    CONTEXT_OVERFLOW = "context_overflow"
    INVALID_REQUEST = "invalid_request"
    CONTENT_POLICY = "content_policy"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


_RETRYABLE = {ErrorCategory.RATE_LIMIT, ErrorCategory.NETWORK, ErrorCategory.SERVER}
_FALLBACK_ALLOWED = _RETRYABLE | {ErrorCategory.CONTEXT_OVERFLOW}


class WindcodeError(Exception):
    def __init__(self, message: str, category: ErrorCategory = ErrorCategory.INTERNAL) -> None:
        super().__init__(message)
        self.category = category

    @property
    def retryable(self) -> bool:
        return self.category in _RETRYABLE

    @property
    def fallback_allowed(self) -> bool:
        return self.category in _FALLBACK_ALLOWED
