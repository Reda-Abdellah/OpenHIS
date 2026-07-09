import asyncio, os, logging, sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from database import init_db
from routers import patients, orders, reports
from openmrs_sync import sync_loop
import log_config

log_config.configure("ris")
log = logging.getLogger("ris")

ROOT_PATH = os.environ.get("ROOT_PATH", "")

_REQUIRED_ENV = ["KEYCLOAK_URL", "KEYCLOAK_TOKEN_URL", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET"]


def _check_env() -> None:
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    init_db()
    log.info("RIS v3.3 ready — db=%s", os.environ.get("DB_PATH", "/data/ris.db"))
    task = asyncio.create_task(sync_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


from jwt_auth import JWTMiddleware
from openhis_sdk.metrics import MetricsMiddleware, metrics_router

app = FastAPI(title="RIS — Radiology Information System", version="3.3.0", root_path=ROOT_PATH, lifespan=lifespan)
app.add_middleware(JWTMiddleware, extra_public_prefixes=("/static/",))
app.add_middleware(MetricsMiddleware, service="ris")
app.include_router(metrics_router)


@app.get("/api/auth/config")
def auth_config():
    """Public endpoint: returns OIDC config needed by the browser SPA for PKCE login."""
    from openhis_sdk.auth import KEYCLOAK_URL, KEYCLOAK_REALM
    return {
        "keycloak_url": os.environ.get("KEYCLOAK_PUBLIC_URL", KEYCLOAK_URL),
        "realm":        KEYCLOAK_REALM,
        "client_id":    os.environ.get("KEYCLOAK_SPA_CLIENT_ID", "openhis-spa"),
    }

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(patients.router)
app.include_router(orders.router)
app.include_router(reports.router)


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ris", "version": "3.3.0",
            "db": os.environ.get("DB_PATH", "/data/ris.db")}
