import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import ROOT_PATH
from app.routers import health, feed, events, audit, fhir, context
from app.db.audit import init_audit_db
from app import worker, registry
from app.log_config import configure as _configure_logging

_configure_logging("integration-hub")

_REQUIRED_ENV = [
    "KEYCLOAK_URL",
    "KEYCLOAK_TOKEN_URL",
    "KEYCLOAK_CLIENT_ID",
    "KEYCLOAK_CLIENT_SECRET",
    "ODOO_ADMIN_PASS",
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
    from app.bus_consumer import consume_loop
    tasks = [
        asyncio.create_task(worker.poll_loop()),
        asyncio.create_task(consume_loop()),
    ]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    await worker.bus.close()


from app.jwt_auth import JWTMiddleware
from openhis_sdk.metrics import MetricsMiddleware, metrics_router

app = FastAPI(title="Integration Hub", version="1.1.0", root_path=ROOT_PATH, lifespan=lifespan)
# /fhir/metadata is the FHIR discovery endpoint: it carries no PHI and FHIR
# clients fetch it *before* they can authenticate, so it stays token-free.
app.add_middleware(JWTMiddleware, extra_public_prefixes=("/fhir/metadata",))
app.add_middleware(MetricsMiddleware, service="integration-hub")
app.include_router(metrics_router)
app.include_router(health.router)
app.include_router(feed.router)
app.include_router(events.router)
app.include_router(audit.router)
app.include_router(fhir.router)
app.include_router(context.router)
