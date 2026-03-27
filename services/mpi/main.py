import asyncio, os, logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import patients, crossref, matching, sync, audit
import bus_consumer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mpi")

ROOT_PATH = os.environ.get('ROOT_PATH', '')
app = FastAPI(title="MPI", version="1.0.0", root_path=ROOT_PATH)

for r in [patients.router, crossref.router, matching.router, sync.router, audit.router]:
    app.include_router(r)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(bus_consumer.consume_loop())
    log.info("MPI v1.0 ready")


@app.get("/api/health")
def health():
    with get_db() as db:
        counts = dict(
            master_patients = db.execute("SELECT COUNT(*) FROM master_patients WHERE status='active'").fetchone()[0],
            cross_references= db.execute("SELECT COUNT(*) FROM cross_references").fetchone()[0],
            pending_matches = db.execute("SELECT COUNT(*) FROM match_candidates WHERE status='pending'").fetchone()[0],
        )
    return {"status": "ok", "service": "mpi", "version": "1.0.0", **counts}
