import asyncio, logging, os, sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from security import hash_password, audit
from routers  import auth, users, services, config, audit as audit_router, announcements, registry, platform, profiles, events as events_router
import log_config

log_config.configure("admin")
log = logging.getLogger('admin')

ROOT_PATH = os.environ.get('ROOT_PATH', '')

_REQUIRED_ENV = ["ADMIN_PASS"]


def _check_env() -> None:
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    init_db()
    _seed_default_admin()
    from routers.registry import seed_base_services
    seed_base_services()
    task = asyncio.create_task(_purge_loop())
    log.info("Admin Dashboard v1.0 ready")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Admin Dashboard", version="1.0.0", root_path=ROOT_PATH, lifespan=lifespan)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(services.router)
app.include_router(config.router)
app.include_router(audit_router.router)
app.include_router(announcements.router)
app.include_router(registry.router)
app.include_router(platform.router)
app.include_router(profiles.router)
app.include_router(events_router.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


def _seed_default_admin():
    default_user = os.environ.get('ADMIN_USER', 'admin')
    default_pass = os.environ.get('ADMIN_PASS')
    with get_db() as db:
        if not db.execute(
            "SELECT 1 FROM admin_users WHERE username=?", (default_user,)
        ).fetchone():
            db.execute(
                "INSERT INTO admin_users(username,password,role) VALUES(?,?,?)",
                (default_user, hash_password(default_pass), 'superadmin')
            )
            log.info(f"Seeded default admin user '{default_user}'")


async def _purge_loop():
    from security import purge_expired_sessions
    while True:
        await asyncio.sleep(3600)
        purge_expired_sessions()


@app.get('/api/health')
def health():
    with get_db() as db:
        users_ct  = db.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
        audit_ct  = db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        active_ct = db.execute(
            "SELECT COUNT(*) FROM admin_sessions"
            " WHERE expires_at > datetime('now')"
        ).fetchone()[0]
        ann_ct    = db.execute(
            "SELECT COUNT(*) FROM announcements WHERE active=1"
        ).fetchone()[0]
    return {
        "status":               "ok",
        "service":              "admin",
        "version":              "1.0.0",
        "admin_users":          users_ct,
        "active_sessions":      active_ct,
        "audit_entries":        audit_ct,
        "active_announcements": ann_ct,
    }
