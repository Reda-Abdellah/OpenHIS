import os
import logging
import asyncio
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

from analyzers import analyze

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai-service")

app = FastAPI(title="AI Analysis Service", version="4.1.0")

ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://orthanc:8042")

# ── in-memory result store  (instance_id → result dict) ──────────────────────
results: dict[str, dict] = {}


# ── models ────────────────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    instance_id: str


# ── background analysis task ──────────────────────────────────────────────────
async def _run_analysis(instance_id: str):
    log.info("Analysis started → %s", instance_id)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{ORTHANC_URL}/instances/{instance_id}/file")
            if r.status_code != 200:
                raise RuntimeError(f"Orthanc returned {r.status_code}")
            dicom_bytes = r.content

        # run in thread so we don't block the event loop
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, analyze, dicom_bytes, instance_id
        )
        result["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
        results[instance_id]  = result
        log.info("Analysis done  → %s  [%d ms]  normal=%s",
                 instance_id, result["analysis_ms"], result["normal"])

    except Exception as e:
        log.exception("Analysis failed → %s", instance_id)
        results[instance_id] = {
            "instance_id": instance_id,
            "status"     : "error",
            "error"      : str(e),
            "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        }


# ── routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status" : "ok",
        "service": "ai-service",
        "version": "4.1.0",
        "cached" : len(results),
    }


@app.post("/analyze", status_code=202)
async def trigger_analysis(body: AnalyzeRequest, bg: BackgroundTasks):
    iid = body.instance_id
    if iid in results and results[iid].get("status") == "completed":
        # already done — return immediately
        return results[iid]

    results[iid] = {
        "instance_id": iid,
        "status"     : "processing",
        "queued_at"  : datetime.now().isoformat(timespec="seconds"),
    }
    bg.add_task(_run_analysis, iid)
    return results[iid]


@app.get("/results")
def list_results(limit: int = 50):
    items = sorted(
        results.values(),
        key=lambda r: r.get("analyzed_at", r.get("queued_at", "")),
        reverse=True,
    )
    return items[:limit]


@app.get("/results/{instance_id}")
def get_result(instance_id: str):
    if instance_id not in results:
        raise HTTPException(404, "No analysis found for this instance. "
                                 "POST to /analyze first.")
    return results[instance_id]


@app.delete("/results/{instance_id}", status_code=204)
def delete_result(instance_id: str):
    results.pop(instance_id, None)
