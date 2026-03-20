import os, logging, uuid
from collections import deque
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from presets import MODALITY_PRESETS
from dicom_factory import build_dicom, SUPPORTED

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("simulator")

app = FastAPI(title="DICOM Acquisition Simulator", version="2.3.0")

STATIC_DIR  = os.path.join(os.path.dirname(__file__), "static")
ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://orthanc:8042")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ── in-memory job history (last 50) ──────────────────────────────────────────
job_history: deque = deque(maxlen=50)


# ── models ────────────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    modality : str
    params   : dict[str, Any]
    patient  : dict[str, Any]


# ── routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "simulator",
            "version": "2.3.0", "supported": SUPPORTED}


@app.get("/api/presets")
def get_presets():
    return MODALITY_PRESETS


@app.get("/api/orthanc-status")
async def orthanc_status():
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{ORTHANC_URL}/system")
            d = r.json()
            return {"reachable": True,
                    "version": d.get("Version","?"), "name": d.get("Name","?")}
    except Exception as e:
        return {"reachable": False, "error": str(e)}


@app.get("/api/jobs")
def get_jobs():
    return list(reversed(job_history))   # newest first


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    mod = req.modality.upper()
    if mod not in SUPPORTED:
        raise HTTPException(422, f"Modality '{mod}' not supported. Available: {SUPPORTED}")

    # 1 — build DICOM(s)
    try:
        dicom_list = build_dicom(mod, req.params, req.patient)
    except Exception as e:
        log.exception("DICOM build failed")
        raise HTTPException(500, f"Build error: {e}")

    log.info("Built %d %s instance(s)  body=%s",
             len(dicom_list), mod, req.params.get("bodyPart","?"))

    # 2 — upload each instance to Orthanc
    instance_ids: list[str] = []
    total_bytes  = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, raw in enumerate(dicom_list):
            r = await client.post(
                f"{ORTHANC_URL}/instances",
                content=raw,
                headers={"Content-Type": "application/dicom"},
            )
            if r.status_code not in (200, 201):
                raise HTTPException(
                    502,
                    f"Orthanc rejected instance {i+1}/{len(dicom_list)}: "
                    f"HTTP {r.status_code} — {r.text[:200]}"
                )
            instance_ids.append(r.json().get("ID", ""))
            total_bytes += len(raw)

    log.info("Stored %d instance(s) → %s", len(instance_ids), instance_ids)

    patient_name = str(req.patient.get("patientName", "UNKNOWN"))
    body_part    = str(req.params.get("bodyPart", ""))
    is_xray      = mod in ("CR", "DX")

    # 3 — record in job history
    job = {
        "id"          : str(uuid.uuid4())[:8],
        "timestamp"   : datetime.now().isoformat(timespec="seconds"),
        "modality"    : mod,
        "bodyPart"    : body_part,
        "patientName" : patient_name,
        "count"       : len(instance_ids),
        "instance_id" : instance_ids,
        "size_bytes"  : total_bytes,
        "ohif_url"    : "/",
        "ai_panel_url": f"/ai-panel.html?instance_id={instance_ids[0]}"
                         if is_xray and instance_ids else None,
    }
    job_history.append(job)

    return job
