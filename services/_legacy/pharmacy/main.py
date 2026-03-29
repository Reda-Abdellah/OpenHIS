import os, logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import catalog, prescriptions, dispensing, mar, stock

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pharmacy")

ROOT_PATH = os.environ.get('ROOT_PATH', '')
app = FastAPI(title="Pharmacy", version="1.0.0", root_path=ROOT_PATH)

for r in [catalog.router, prescriptions.router, dispensing.router, mar.router, stock.router]:
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
    log.info("Pharmacy v1.0 ready")

@app.get("/api/health")
def health():
    with get_db() as db:
        counts = dict(
            medications  = db.execute("SELECT COUNT(*) FROM medications").fetchone()[0],
            prescriptions= db.execute("SELECT COUNT(*) FROM prescriptions").fetchone()[0],
            pending      = db.execute("SELECT COUNT(*) FROM prescriptions WHERE status='pending'").fetchone()[0],
        )
    return {"status": "ok", "service": "pharmacy", "version": "1.0.0", **counts}
