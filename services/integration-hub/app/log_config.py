"""
Structured JSON logging setup for OpenHIS services.

Call ``configure()`` once at startup (before ``app = FastAPI(...)``).
Falls back to standard text logging if python-json-logger is not installed.

Environment:
  LOG_LEVEL  — default INFO
  LOG_FORMAT — "json" (default) or "text"
"""
import logging
import os


def configure(service_name: str = "") -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.environ.get("LOG_FORMAT", "json").lower()

    if fmt == "json":
        try:
            from pythonjsonlogger.json import JsonFormatter
            handler = logging.StreamHandler()
            fields = "%(asctime)s %(levelname)s %(name)s %(message)s"
            handler.setFormatter(JsonFormatter(fields, rename_fields={
                "asctime": "time",
                "levelname": "level",
                "name": "logger",
            }))
            if service_name:
                # Inject service name into every log record
                old_factory = logging.getLogRecordFactory()

                def record_factory(*args, **kwargs):
                    record = old_factory(*args, **kwargs)
                    record.service = service_name
                    return record

                logging.setLogRecordFactory(record_factory)
            logging.basicConfig(level=level, handlers=[handler], force=True)
            return
        except ImportError:
            pass  # fall through to text format

    # Text fallback
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    )
