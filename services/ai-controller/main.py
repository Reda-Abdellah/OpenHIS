import asyncio
import os
import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from database import init_db, get_db, rows_to_list
from routers import pipelines, rules, jobs, artifacts, saveback
import bus_consumer
import orthanc_client as oc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai-controller")

app = FastAPI(title="AI Controller", version="1.0.0", root_path="")

# ── routers ───────────────────────────────────────────────────────────────────
app.include_router(pipelines.router)
app.include_router(rules.router)
app.include_router(jobs.router)
app.include_router(artifacts.router)
app.include_router(saveback.router)

# ── static UI ─────────────────────────────────────────────────────────────────
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


# ── startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(bus_consumer.consume_loop())
    log.info("AI Controller v1.0 ready")


# ── health ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    with get_db() as db:
        counts = {
            "pipelines": db.execute("SELECT count(*) FROM pipelines").fetchone()[0],
            "jobs":      db.execute("SELECT count(*) FROM jobs").fetchone()[0],
        }
    return {"status": "ok", "service": "ai-controller", "version": "1.0.0", **counts}


# ── Orthanc webhook (per-instance, deduplicated at series level) ───────────────
class InstanceTrigger(BaseModel):
    instance_id: str


@app.post("/api/trigger-instance", status_code=202)
async def trigger_instance(body: InstanceTrigger, bg: BackgroundTasks):
    """
    Called by orthanc/plugin.py for every stored instance.
    Resolves the series, checks matching auto-trigger rules,
    and submits jobs not already queued for this series.
    """
    try:
        instance_meta = await oc.get_instance_metadata(body.instance_id)
    except Exception as exc:
        raise HTTPException(503, f"Orthanc unreachable: {exc}")

    series_id = instance_meta.get("ParentSeries", "")
    if not series_id:
        return {"skipped": True, "reason": "no parent series"}

    try:
        series_meta = await oc.get_series_metadata(series_id)
    except Exception:
        return {"skipped": True, "reason": "series not yet available"}

    tags      = series_meta.get("MainDicomTags", {})
    modality  = tags.get("Modality", "").upper()
    body_part = tags.get("BodyPartExamined", "").upper()

    with get_db() as db:
        rules_rows = rows_to_list(db.execute("""
            SELECT r.*, p.docker_image FROM rules r
            JOIN pipelines p ON p.id=r.pipeline_id
            WHERE r.auto_trigger=1 AND r.enabled=1 AND p.enabled=1
            ORDER BY r.priority DESC
        """).fetchall())

    launched = []
    for rule in rules_rows:
        if not _matches_rule(rule, modality, body_part):
            continue
        if _check_existing_job(series_id, rule["pipeline_id"]):
            continue
        from routers.jobs import trigger_job, TriggerRequest
        req = TriggerRequest(
            pipeline_id=rule["pipeline_id"],
            orthanc_series_id=series_id,
            rule_id=rule["id"],
            trigger_type="AUTO",
        )
        try:
            result = await trigger_job(req, bg)
            launched.append(result)
        except Exception as exc:
            log.warning(f"Auto-trigger failed for rule {rule['id']}: {exc}")

    return {"launched": len(launched), "series_id": series_id, "jobs": launched}


def _matches_rule(rule: dict, modality: str, body_part: str) -> bool:
    if rule.get("modality"):
        allowed = [m.strip().upper() for m in rule["modality"].split(",")]
        if modality and modality not in allowed:
            return False
    if rule.get("body_part"):
        allowed = [b.strip().upper() for b in rule["body_part"].split(",")]
        if body_part and body_part not in allowed:
            return False
    return True


def _check_existing_job(orthanc_series_id: str, pipeline_id: str) -> bool:
    with get_db() as db:
        row = db.execute("""
            SELECT 1 FROM jobs
            WHERE orthanc_series_id=? AND pipeline_id=?
            AND status IN ('PENDING','RUNNING','COMPLETED')
        """, (orthanc_series_id, pipeline_id)).fetchone()
    return row is not None


# ── Orthanc series browser ────────────────────────────────────────────────────
@app.get("/api/orthanc/series")
async def list_orthanc_series():
    """Browse Orthanc series for manual job triggering."""
    try:
        async with __import__("httpx").AsyncClient(timeout=10) as c:
            resp = await c.get(f"{oc.ORTHANC_URL}/series")
            resp.raise_for_status()
            series_ids = resp.json()

        result = []
        for sid in series_ids[:50]:
            try:
                meta     = await oc.get_series_metadata(sid)
                tags     = meta.get("MainDicomTags", {})
                study_id = meta.get("ParentStudy", "")
                study_meta = await oc.get_study_metadata(study_id) if study_id else {}
                patient  = study_meta.get("PatientMainDicomTags", {})
                result.append({
                    "orthanc_series_id": sid,
                    "modality":          tags.get("Modality", ""),
                    "body_part":         tags.get("BodyPartExamined", ""),
                    "series_uid":        tags.get("SeriesInstanceUID", ""),
                    "instance_count":    len(meta.get("Instances", [])),
                    "patient_name":      patient.get("PatientName", "UNKNOWN"),
                    "description":       tags.get("SeriesDescription", ""),
                })
            except Exception:
                pass
        return result
    except Exception as exc:
        raise HTTPException(503, f"Orthanc unavailable: {exc}")
