"""
openhis_sdk — shared library for OpenHIS services.

Canonical source for cross-cutting concerns:
  - JWT validation middleware (auth.py)
  - Redis Streams publish/consume helpers (bus.py)
  - Structured JSON logging setup (logging.py)
  - Retry decorator (retry.py)
  - Request logging FastAPI middleware (middleware.py)

Services install this via:
    pip install -e ../../libs/openhis_sdk   # local dev
    pip install openhis-sdk==0.1.0           # CI / image build
"""

from .auth import JWTMiddleware
from .bus import BusConsumer, publish_event
from .logging import configure_logging
from .middleware import RequestLoggingMiddleware
from .retry import with_retry

__all__ = [
    "JWTMiddleware",
    "BusConsumer",
    "publish_event",
    "configure_logging",
    "RequestLoggingMiddleware",
    "with_retry",
]
