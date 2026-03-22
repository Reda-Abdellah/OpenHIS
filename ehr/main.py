import os, logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import patients, encounters, orders, cdss, scheduling, billing

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ehr")

ROOT_PATH = os.environ.get("ROOT_PATH", os.environ.get("ROOTPATH", ""))
app = FastAPI(title="EHR", version="1.0.0", root_path=ROOT_PATH)

for r in (patients.router, encounters.router, orders.router,
          cdss.router, scheduling.router, billing.router):
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
    log.info("EHR v1.0 ready")

@app.get("/api/health")
def health():
    with get_db() as db:
        counts = dict(
            patients   = db.execute("SELECT count(*) FROM patients").fetchone()[0],
            encounters = db.execute("SELECT count(*) FROM encounters").fetchone()[0],
            orders     = db.execute("SELECT count(*) FROM clinicalorders").fetchone()[0],
            cdssalerts = db.execute("SELECT count(*) FROM cdssalerts WHERE acknowledged=0").fetchone()[0],
        )
    return {"status": "ok", "service": "ehr", "version": "1.0.0", "counts": counts}
