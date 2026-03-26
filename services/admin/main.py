import asyncio, logging, os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from security import hash_password, audit
from routers  import auth, users, services, config, audit as audit_router, announcements, registry

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('admin')

ROOT_PATH = os.environ.get('ROOT_PATH', '')
app = FastAPI(title="Admin Dashboard", version="1.0.0", root_path=ROOT_PATH)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(services.router)
app.include_router(config.router)
app.include_router(audit_router.router)
app.include_router(announcements.router)
app.include_router(registry.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


@app.on_event('startup')
async def startup():
    init_db()
    _seed_default_admin()
    from routers.registry import seed_base_services
    seed_base_services()
    asyncio.create_task(_purge_loop())
    log.info("Admin Dashboard v1.0 ready")


def _seed_default_admin():
    default_user = os.environ.get('ADMIN_USER', 'admin')
    default_pass = os.environ.get('ADMIN_PASS', 'admin123')
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
