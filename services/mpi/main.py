import asyncio, os, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import patients, crossref, matching, sync, audit
import bus_consumer
from openhis_sdk.logging import configure

configure("mpi")
log = logging.getLogger("mpi")

ROOT_PATH = os.environ.get('ROOT_PATH', '')


@asynccontextmanager
async def lifespan(app: FastAPI):
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

app = FastAPI(title="MPI", version="1.0.0", root_path=ROOT_PATH, lifespan=lifespan)
app.add_middleware(JWTMiddleware)

for r in [patients.router, crossref.router, matching.router, sync.router, audit.router]:
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
