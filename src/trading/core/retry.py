"""Retry utilities with exponential backoff and exception classification."""

import logging
import time
from functools import wraps
from typing import Type

logger = logging.getLogger(__name__)


class TransientError(Exception):
    """Temporary error that may resolve on retry (network timeout, rate limit)."""
    pass


class PermanentError(Exception):
    """Non-recoverable error (auth failure, invalid config, business logic)."""
    pass


# Known transient exception types from common libraries
_TRANSIENT_TYPES: tuple[Type[BaseException], ...] = (
    TransientError,
    ConnectionError,
    TimeoutError,
    OSError,
)


def is_transient(exc: BaseException) -> bool:
    """Classify whether an exception is likely transient."""
    if isinstance(exc, _TRANSIENT_TYPES):
        return True
    # Binance connector raises generic exceptions with status codes
    msg = str(exc).lower()
    transient_indicators = (
        "timeout", "timed out", "connection reset",
        "rate limit", "429", "503", "502", "504",
        "temporary", "retry", "network",
    )
    return any(indicator in msg for indicator in transient_indicators)


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    transient_only: bool = True,
):
    """Decorator: retry with exponential backoff for transient errors.

    Args:
        max_attempts: Maximum number of attempts (including the first call).
        base_delay: Initial delay in seconds between retries.
        max_delay: Maximum delay cap in seconds.
        transient_only: If True, only retry transient errors; re-raise permanent ones immediately.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except PermanentError:
                    raise
                except Exception as exc:
                    last_exc = exc
                    if transient_only and not is_transient(exc):
                        raise
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "Retry %d/%d for %s after %.1fs: %s",
                        attempt, max_attempts, func.__name__, delay, exc,
                    )
                    time.sleep(delay)
            # Should not reach here, but just in case
            if last_exc:
                raise last_exc  # pragma: no cover
        return wrapper
    return decorator
