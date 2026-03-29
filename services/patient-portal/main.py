import asyncio, logging, os, sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers  import auth, me
import log_config

log_config.configure("patient-portal")
log = logging.getLogger('patient-portal')

ROOT_PATH = os.environ.get('ROOT_PATH', '')

_REQUIRED_ENV = ["OPENMRS_USER", "OPENMRS_PASS", "OPENELIS_USER", "OPENELIS_PASS"]


def _check_env() -> None:
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    init_db()
    task = asyncio.create_task(_session_purge_loop())
    log.info("Patient Portal v1.0 ready")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


from jwt_auth import JWTMiddleware

app = FastAPI(title="Patient Portal", version="1.0.0", root_path=ROOT_PATH, lifespan=lifespan)
app.add_middleware(JWTMiddleware)
app.include_router(auth.router)
app.include_router(me.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/api/health')
def health():
    with get_db() as db:
        sessions = db.execute(
            "SELECT COUNT(*) FROM sessions WHERE expires_at > datetime('now')"
        ).fetchone()[0]
        requests = db.execute(
            "SELECT COUNT(*) FROM appointment_requests"
        ).fetchone()[0]
    return {
        "status":               "ok",
        "service":              "patient-portal",
        "version":              "1.0.0",
        "active_sessions":      sessions,
        "appointment_requests": requests,
    }


@app.get('/', response_class=HTMLResponse)
@app.get('/{path:path}', response_class=HTMLResponse,
         include_in_schema=False)
async def spa(path: str = ""):
    """Serve SPA for all non-API routes."""
    if path.startswith("api/"):
        from fastapi import HTTPException
        raise HTTPException(404)
    with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


async def _session_purge_loop():
    """Purge expired sessions every hour."""
    from auth import purge_expired
    while True:
        await asyncio.sleep(3600)
        purge_expired()

