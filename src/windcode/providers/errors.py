from __future__ import annotations

import asyncio
from typing import cast

import aiohttp

from windcode.domain.errors import ErrorCategory, WindcodeError


class ProviderError(WindcodeError):
    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, category)
        self.status_code = status_code


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(error, "response", None)
    value = getattr(response, "status", None)
    return value if isinstance(value, int) else None


def _category_from_status(status: int, message: str) -> ErrorCategory:
    if status in {401, 403}:
        return ErrorCategory.AUTHENTICATION
    if status == 429:
        return ErrorCategory.RATE_LIMIT
    if status in {408, 409, 425}:
        return ErrorCategory.NETWORK
    if status >= 500:
        return ErrorCategory.SERVER
    lowered = message.casefold()
    if status == 400 and any(
        phrase in lowered for phrase in ("context length", "context window", "too many tokens")
    ):
        return ErrorCategory.CONTEXT_OVERFLOW
    if status in {400, 404, 405, 422}:
        return ErrorCategory.INVALID_REQUEST
    return ErrorCategory.INTERNAL


def map_provider_error(error: BaseException) -> WindcodeError:
    if isinstance(error, WindcodeError):
        return error
    if isinstance(error, asyncio.CancelledError):
        return ProviderError("model request cancelled", ErrorCategory.CANCELLED)
    if isinstance(error, (TimeoutError, aiohttp.ClientConnectionError)):
        return ProviderError(str(error) or "model network request failed", ErrorCategory.NETWORK)
    if isinstance(error, aiohttp.ClientError):
        status = _status_code(error)
        category = (
            _category_from_status(status, str(error))
            if status is not None
            else ErrorCategory.NETWORK
        )
        return ProviderError(str(error), category, status_code=status)

    status = _status_code(error)
    if status is not None:
        return ProviderError(
            str(error),
            _category_from_status(status, str(error)),
            status_code=status,
        )

    class_name = type(error).__name__.casefold()
    if "authentication" in class_name or "permission" in class_name:
        category = ErrorCategory.AUTHENTICATION
    elif "ratelimit" in class_name or "rate_limit" in class_name:
        category = ErrorCategory.RATE_LIMIT
    elif "connection" in class_name or "timeout" in class_name:
        category = ErrorCategory.NETWORK
    elif "content" in class_name and ("filter" in class_name or "policy" in class_name):
        category = ErrorCategory.CONTENT_POLICY
    elif "badrequest" in class_name or "invalidrequest" in class_name:
        category = _category_from_status(400, str(error))
    else:
        category = ErrorCategory.INTERNAL
    return ProviderError(str(error), category, status_code=cast(int | None, status))


def error_from_http_status(status_code: int, message: str) -> ProviderError:
    return ProviderError(
        message,
        _category_from_status(status_code, message),
        status_code=status_code,
    )
