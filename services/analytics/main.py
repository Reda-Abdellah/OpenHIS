import asyncio, logging, os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import metrics, export

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('analytics')

ROOT_PATH = os.environ.get('ROOT_PATH', '')
app = FastAPI(title="Analytics", version="1.0.0", root_path=ROOT_PATH)
app.include_router(metrics.router)
app.include_router(export.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


@app.on_event('startup')
async def startup():
    init_db()
    interval = int(os.environ.get('COLLECT_INTERVAL_MIN', '5'))
    from scheduler import start_scheduler
    start_scheduler(interval)
    asyncio.create_task(_first_collect())
    log.info(f"Analytics v1.0 ready — interval={interval} min")


async def _first_collect():
    await asyncio.sleep(3)   # give other services time to start
    from collector import collect_and_store
    await collect_and_store()


@app.on_event('shutdown')
async def shutdown():
    from scheduler import stop_scheduler
    stop_scheduler()


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
