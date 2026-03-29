"""
Structured JSON logging setup for OpenHIS services.

Usage:
    from openhis_sdk.logging import configure_logging
    configure_logging(level="INFO")   # call once at service startup
"""
import logging
import os
import sys


def configure_logging(level: str | None = None) -> None:
    """
    Configure root logger for structured output.

    In production (LOG_FORMAT=json) emits JSON lines.
    In development (default) emits human-readable text.
    """
    log_level = level or os.environ.get("LOG_LEVEL", "INFO").upper()
    log_format = os.environ.get("LOG_FORMAT", "text").lower()

    if log_format == "json":
        try:
            import structlog

            structlog.configure(
                processors=[
                    structlog.processors.TimeStamper(fmt="iso"),
                    structlog.stdlib.add_log_level,
                    structlog.processors.JSONRenderer(),
                ],
                wrapper_class=structlog.stdlib.BoundLogger,
                logger_factory=structlog.stdlib.LoggerFactory(),
            )
        except ImportError:
            pass  # fall through to stdlib config

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )
