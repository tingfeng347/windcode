import pytest

from windcode.domain.errors import ErrorCategory, WindcodeError


@pytest.mark.parametrize(
    ("category", "retryable", "fallback"),
    [
        (ErrorCategory.AUTHENTICATION, False, False),
        (ErrorCategory.RATE_LIMIT, True, True),
        (ErrorCategory.NETWORK, True, True),
        (ErrorCategory.SERVER, True, True),
        (ErrorCategory.CONTEXT_OVERFLOW, False, True),
        (ErrorCategory.INVALID_REQUEST, False, False),
        (ErrorCategory.CONTENT_POLICY, False, False),
        (ErrorCategory.CANCELLED, False, False),
    ],
)
def test_error_policy(category: ErrorCategory, retryable: bool, fallback: bool) -> None:
    error = WindcodeError("boom", category)

    assert error.retryable is retryable
    assert error.fallback_allowed is fallback
