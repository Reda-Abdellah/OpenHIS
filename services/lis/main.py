import os, logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers import specimens, orders, results, qc, instruments, patients

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lis")

app = FastAPI(title="LIS", version="1.0.0", root_path=os.environ.get("ROOT_PATH", ""))

for r in (patients.router, specimens.router, orders.router,
          results.router, qc.router, instruments.router):
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
    log.info("LIS v1.0 ready")

@app.get("/api/health")
def health():
    with get_db() as db:
        counts = {
            "patients":  db.execute("SELECT count(*) FROM lab_patients").fetchone()[0],
            "specimens": db.execute("SELECT count(*) FROM specimens").fetchone()[0],
            "orders":    db.execute("SELECT count(*) FROM lab_orders").fetchone()[0],
            "results":   db.execute("SELECT count(*) FROM lab_results").fetchone()[0],
        }
    return {"status": "ok", "service": "lis", "version": "1.0.0", "counts": counts}
