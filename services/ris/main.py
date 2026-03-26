import asyncio, os, logging
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from database import init_db
from routers import patients, orders, reports
from openmrs_sync import sync_loop

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ris")

ROOT_PATH = os.environ.get("ROOT_PATH", "")
app = FastAPI(title="RIS — Radiology Information System", version="3.3.0", root_path=ROOT_PATH)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(patients.router)
app.include_router(orders.router)
app.include_router(reports.router)


@app.on_event("startup")
async def startup():
    init_db()
    log.info("RIS v3.3 ready — db=%s", os.environ.get("DB_PATH", "/data/ris.db"))
    asyncio.create_task(sync_loop())


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ris", "version": "3.3.0",
            "db": os.environ.get("DB_PATH", "/data/ris.db")}
