"""
Request logging middleware for FastAPI/Starlette.

Logs method, path, status code, and latency for every request.

Usage:
    from openhis_sdk.middleware import RequestLoggingMiddleware
    app.add_middleware(RequestLoggingMiddleware)
"""
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = logging.getLogger("openhis_sdk.access")

# /metrics: scraped periodically (openhis_sdk.metrics) — keep it out of the
# access log for the same reason as /api/health.
_SKIP_PATHS = {"/api/health", "/docs", "/redoc", "/openapi.json", "/metrics"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000

        log.info(
            "%s %s → %d (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            latency_ms,
        )
        return response
