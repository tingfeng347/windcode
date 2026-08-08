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
    EXTENSION = "extension"
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


class RequiredExtensionError(WindcodeError, RuntimeError):
    def __init__(
        self,
        failed_sources: tuple[str, ...],
        *,
        extension_kind: str = "extension",
        stage: str = "execution",
    ) -> None:
        self.failed_sources = tuple(sorted(failed_sources))
        self.extension_kind = extension_kind
        self.stage = stage
        displayed = self.failed_sources[:8]
        overflow = len(self.failed_sources) - len(displayed)
        sources = ", ".join(displayed)
        if overflow:
            sources = f"{sources} (+{overflow} more)"
        failure = (
            f"required {extension_kind} startup blocked by"
            if stage == "startup"
            else f"required {extension_kind} failed during {stage}"
        )
        super().__init__(
            f"{failure}: {sources}. "
            "Check configuration, credentials, network policy, or disable the failing extension, "
            "then retry.",
            ErrorCategory.EXTENSION,
        )


class RequiredExtensionStartupError(RequiredExtensionError):
    def __init__(
        self,
        failed_sources: tuple[str, ...],
        *,
        extension_kind: str = "extension",
    ) -> None:
        super().__init__(failed_sources, extension_kind=extension_kind, stage="startup")
