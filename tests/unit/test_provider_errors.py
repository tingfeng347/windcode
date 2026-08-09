import asyncio

import pytest

from windcode.domain.errors import ErrorCategory, WindcodeError
from windcode.providers.errors import ProviderError, map_provider_error


class StatusError(Exception):
    def __init__(self, status_code: int, message: str = "error") -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("error", "category", "retryable", "fallback"),
    [
        (StatusError(401), ErrorCategory.AUTHENTICATION, False, False),
        (StatusError(429), ErrorCategory.RATE_LIMIT, True, True),
        (StatusError(503), ErrorCategory.SERVER, True, True),
        (StatusError(400, "context length exceeded"), ErrorCategory.CONTEXT_OVERFLOW, False, True),
        (StatusError(400), ErrorCategory.INVALID_REQUEST, False, False),
        (TimeoutError(), ErrorCategory.NETWORK, True, True),
        (asyncio.CancelledError(), ErrorCategory.CANCELLED, False, False),
    ],
)
def test_maps_error_matrix(
    error: BaseException,
    category: ErrorCategory,
    retryable: bool,
    fallback: bool,
) -> None:
    mapped = map_provider_error(error)

    assert mapped.category is category
    assert mapped.retryable is retryable
    assert mapped.fallback_allowed is fallback


def test_preserves_existing_windcode_error() -> None:
    error = WindcodeError("already mapped", ErrorCategory.SERVER)
    assert map_provider_error(error) is error


def test_provider_error_exposes_http_status() -> None:
    mapped = map_provider_error(StatusError(429))
    assert isinstance(mapped, ProviderError)
    assert mapped.status_code == 429
