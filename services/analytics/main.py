import asyncio, logging, os, sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import metrics, export
import bus_consumer
import log_config

log_config.configure("analytics")
log = logging.getLogger('analytics')

ROOT_PATH = os.environ.get('ROOT_PATH', '')

_REQUIRED_ENV = ["OPENMRS_USER", "OPENMRS_PASS", "OPENELIS_USER", "OPENELIS_PASS"]


def _check_env() -> None:
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")


async def _first_collect():
    await asyncio.sleep(3)   # give other services time to start
    from collector import collect_and_store
    await collect_and_store()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    init_db()
    interval = int(os.environ.get('COLLECT_INTERVAL_MIN', '5'))
    from scheduler import start_scheduler
    start_scheduler(interval)
    collect_task = asyncio.create_task(_first_collect())
    bus_task = asyncio.create_task(bus_consumer.consume_loop())
    log.info(f"Analytics v1.0 ready — interval={interval} min")
    yield
    bus_task.cancel()
    collect_task.cancel()
    try:
        await bus_task
    except asyncio.CancelledError:
        pass
    from scheduler import stop_scheduler
    stop_scheduler()


from jwt_auth import JWTMiddleware

app = FastAPI(title="Analytics", version="1.0.0", root_path=ROOT_PATH, lifespan=lifespan)
app.add_middleware(JWTMiddleware)
app.include_router(metrics.router)
app.include_router(export.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


@app.get('/api/health')
def health():
    with get_db() as db:
        count  = db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        latest = db.execute("SELECT MAX(captured_at) FROM snapshots").fetchone()[0]
    return {
        'status':        'ok',
        'service':       'analytics',
        'version':       '1.0.0',
        'total_snapshots': count,
        'last_collected':  latest,
    }
