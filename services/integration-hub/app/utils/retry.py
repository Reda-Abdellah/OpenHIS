"""
Async retry decorator with exponential back-off.

Usage:
    @with_retry(max_attempts=3, base_delay=1.0)
    async def _post_something(...):
        ...
"""
import asyncio
import functools
import logging


def with_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """
    Decorator that retries an async function up to max_attempts times
    with exponential back-off (base_delay * 2^(attempt-1) seconds).
    Raises the last exception if all attempts fail.
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            log = logging.getLogger(fn.__module__)
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts:
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "%s attempt %d/%d failed: %s. Retrying in %.1fs",
                        fn.__name__, attempt, max_attempts, exc, delay,
                    )
                    await asyncio.sleep(delay)
        return wrapper
    return decorator
