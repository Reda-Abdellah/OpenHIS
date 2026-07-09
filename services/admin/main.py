import asyncio, logging, os, sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import services, config, audit as audit_router, announcements, registry, platform, profiles, events as events_router, identity
import log_config

log_config.configure("admin")
log = logging.getLogger('admin')

ROOT_PATH = os.environ.get('ROOT_PATH', '')

_REQUIRED_ENV = ["KEYCLOAK_URL"]


def _check_env() -> None:
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    init_db()
    from routers.registry import seed_base_services
    seed_base_services()
    # DEF-002: bridge bus events (patient.synced) into the admin audit log.
    # Guarded on REDIS_URL so unit tests / minimal stacks boot without Redis.
    bus_task = None
    if os.environ.get("REDIS_URL"):
        import bus_consumer
        bus_task = asyncio.create_task(bus_consumer.build_consumer().run())
        log.info("Admin bus consumer started (patient.synced -> audit_log)")
    log.info("Admin Dashboard v2.0 ready (Keycloak-only auth)")
    yield
    if bus_task is not None:
        bus_task.cancel()
        try:
            await bus_task
        except asyncio.CancelledError:
            pass


from openhis_sdk.metrics import MetricsMiddleware, metrics_router

app = FastAPI(title="Admin Dashboard", version="2.0.0", root_path=ROOT_PATH, lifespan=lifespan)
app.add_middleware(MetricsMiddleware, service="admin")
app.include_router(metrics_router)

app.include_router(services.router)
app.include_router(config.router)
app.include_router(audit_router.router)
app.include_router(announcements.router)
app.include_router(registry.router)
app.include_router(platform.router)
app.include_router(profiles.router)
app.include_router(events_router.router)
app.include_router(identity.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


@app.get('/api/auth/config')
def auth_config():
    """Public endpoint: returns OIDC config needed by the browser SPA for PKCE login."""
    from jwt_auth import KEYCLOAK_URL, KEYCLOAK_REALM
    return {
        "keycloak_url": os.environ.get("KEYCLOAK_PUBLIC_URL", KEYCLOAK_URL),
        "realm":        KEYCLOAK_REALM,
        "client_id":    os.environ.get("KEYCLOAK_SPA_CLIENT_ID", "openhis-admin-spa"),
    }


@app.get('/api/health')
def health():
    with get_db() as db:
        audit_ct = db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        ann_ct   = db.execute(
            "SELECT COUNT(*) FROM announcements WHERE active=1"
        ).fetchone()[0]
    return {
        "status":               "ok",
        "service":              "admin",
        "version":              "2.0.0",
        "audit_entries":        audit_ct,
        "active_announcements": ann_ct,
    }
