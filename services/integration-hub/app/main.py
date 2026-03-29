import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import ROOT_PATH
from app.routers import health, feed, events, audit
from app.db.audit import init_audit_db
from app import worker, registry
from app.log_config import configure as _configure_logging

_configure_logging("integration-hub")

_REQUIRED_ENV = [
    "OPENMRS_USER", "OPENMRS_PASS",
    "OPENELIS_USER", "OPENELIS_PASS",
    "ODOO_USER", "ODOO_PASS",
]


def _check_env() -> None:
    import os
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    registry.load()
    await init_audit_db()
    await worker.bus.ensure_stream()
    task = asyncio.create_task(worker.poll_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await worker.bus.close()


from app.jwt_auth import JWTMiddleware

app = FastAPI(title="Integration Hub", version="1.1.0", root_path=ROOT_PATH, lifespan=lifespan)
app.add_middleware(JWTMiddleware)
app.include_router(health.router)
app.include_router(feed.router)
app.include_router(events.router)
app.include_router(audit.router)
