import os, logging
from fastapi import FastAPI
from routers import events

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fhir-bridge")

app = FastAPI(title="FHIR Bridge", version="1.0.0", root_path=os.environ.get("ROOT_PATH", ""))
app.include_router(events.router)

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "fhir-bridge",
        "fhir_server": os.environ.get("FHIR_SERVER_URL", "not configured"),
        "fhir_enabled": os.environ.get("FHIR_ENABLED", "true"),
    }

@app.on_event("startup")
async def startup():
    log.info("FHIR Bridge v1.0 ready")
