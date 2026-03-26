import asyncio
import logging
from fastapi import FastAPI
from app.config import ROOT_PATH
from app.routers import health, feed, events, audit
from app.db.audit import init_audit_db
from app import worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
)

app = FastAPI(title="Integration Hub", version="1.1.0", root_path=ROOT_PATH)
app.include_router(health.router)
app.include_router(feed.router)
app.include_router(events.router)
app.include_router(audit.router)


@app.on_event("startup")
async def startup():
    await init_audit_db()
    asyncio.create_task(worker.poll_loop())
