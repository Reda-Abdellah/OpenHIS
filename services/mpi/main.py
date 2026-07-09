import asyncio, os, sys, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import patients, crossref, matching, sync, audit, fhir
import bus_consumer
from openhis_sdk.logging import configure

configure("mpi")
log = logging.getLogger("mpi")

ROOT_PATH = os.environ.get('ROOT_PATH', '')

# Startup guard (service contract): fail fast instead of booting with auth
# silently disabled — JWTMiddleware becomes a pass-through when KEYCLOAK_URL
# is unset. REDIS_URL and MPI_DATABASE_URL are NOT listed: bus.py degrades
# gracefully without Redis and database.py has a compose-correct default DSN
# (see openhis.service.json env.optional).
_REQUIRED_ENV = ["KEYCLOAK_URL"]


def _missing_env() -> list[str]:
    """Return the names of required env vars that are unset or empty."""
    return [k for k in _REQUIRED_ENV if not os.getenv(k)]


def _check_env() -> None:
    missing = _missing_env()
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    init_db()
    task = asyncio.create_task(bus_consumer.consume_loop())
    log.info("MPI v1.0 ready")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


from openhis_sdk.auth import JWTMiddleware
from openhis_sdk.metrics import MetricsMiddleware, metrics_router

app = FastAPI(title="MPI", version="1.0.0", root_path=ROOT_PATH, lifespan=lifespan)
app.add_middleware(JWTMiddleware)
app.add_middleware(MetricsMiddleware, service="mpi")
app.include_router(metrics_router)

for r in [patients.router, crossref.router, matching.router, sync.router, audit.router, fhir.router]:
    app.include_router(r)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/auth/config")
def auth_config():
    """Public endpoint: returns OIDC config needed by the browser SPA for PKCE login."""
    return {
        "keycloak_url": os.environ.get("KEYCLOAK_PUBLIC_URL", ""),
        "realm":        os.environ.get("KEYCLOAK_REALM", "openhis"),
        "client_id":    os.environ.get("KEYCLOAK_SPA_CLIENT_ID", "openhis-admin-spa"),
    }


@app.get("/api/health")
def health():
    with get_db() as db:
        mp = db.execute("SELECT COUNT(*) AS n FROM master_patients WHERE status='active'").fetchone()["n"]
        xr = db.execute("SELECT COUNT(*) AS n FROM cross_references").fetchone()["n"]
        pm = db.execute("SELECT COUNT(*) AS n FROM match_candidates WHERE status='pending'").fetchone()["n"]
    return {"status": "ok", "service": "mpi", "version": "1.0.0",
            "master_patients": mp, "cross_references": xr, "pending_matches": pm}
