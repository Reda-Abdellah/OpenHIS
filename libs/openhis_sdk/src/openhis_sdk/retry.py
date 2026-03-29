"""
Retry decorator for async functions.

Usage:
    from openhis_sdk.retry import with_retry

    @with_retry(attempts=3, backoff=2.0)
    async def call_external_api():
        ...
"""
import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger("openhis_sdk.retry")
T = TypeVar("T")


def with_retry(
    attempts: int = 3,
    backoff: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable:
    """Decorator: retry an async function up to `attempts` times with exponential backoff."""

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs) -> T:
            delay = backoff
            for attempt in range(1, attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == attempts:
                        raise
                    log.warning("%s attempt %d/%d failed: %s — retrying in %.1fs", fn.__name__, attempt, attempts, exc, delay)
                    await asyncio.sleep(delay)
                    delay *= 2
            raise RuntimeError("unreachable")  # pragma: no cover

        return wrapper

    return decorator
