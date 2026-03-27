import json
import uuid
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from database import get_db, rows_to_list, row_to_dict
from runner import run_job
import orthanc_client as oc

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class TriggerRequest(BaseModel):
    pipeline_id: str
    orthanc_series_id: Optional[str] = None   # imaging jobs
    event_source_id: Optional[str] = None      # clinical jobs (oe_id, omrs_id, …)
    event_payload: Optional[dict] = None       # raw event data for clinical jobs
    rule_id: Optional[int] = None
    trigger_type: str = "MANUAL"


async def trigger_job(req: TriggerRequest, bg: BackgroundTasks) -> dict:
    """Create a job row and enqueue it. Supports both imaging and clinical pipelines."""
    with get_db() as db:
        pipeline = db.execute(
            "SELECT source_type FROM pipelines WHERE id=?", (req.pipeline_id,)
        ).fetchone()
        if not pipeline:
            raise HTTPException(404, f"Pipeline '{req.pipeline_id}' not found")
        source_type = pipeline["source_type"]

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if source_type == "imaging":
        if not req.orthanc_series_id:
            raise HTTPException(400, "orthanc_series_id required for imaging pipelines")
        try:
            series_meta = await oc.get_series_metadata(req.orthanc_series_id)
        except Exception as exc:
            raise HTTPException(503, f"Orthanc unavailable: {exc}")

        tags = series_meta.get("MainDicomTags", {})
        study_id = series_meta.get("ParentStudy", "")
        study_meta, patient, study_tags = {}, {}, {}
        try:
            if study_id:
                study_meta = await oc.get_study_metadata(study_id)
                patient = study_meta.get("PatientMainDicomTags", {})
                study_tags = study_meta.get("MainDicomTags", {})
        except Exception:
            pass

        with get_db() as db:
            db.execute(
                "INSERT INTO jobs (id,pipeline_id,rule_id,series_uid,study_uid,"
                "patient_name,patient_id,modality,body_part,accession_number,"
                "orthanc_series_id,orthanc_study_id,source_type,status,trigger_type,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, req.pipeline_id, req.rule_id,
                    tags.get("SeriesInstanceUID", ""),
                    study_tags.get("StudyInstanceUID", ""),
                    patient.get("PatientName", ""),
                    patient.get("PatientID", ""),
                    tags.get("Modality", ""),
                    tags.get("BodyPartExamined", ""),
                    study_tags.get("AccessionNumber", ""),
                    req.orthanc_series_id, study_id,
                    "imaging", "PENDING", req.trigger_type, now,
                ),
            )
    else:
        # clinical / lab_result / emr_event job
        payload_str = json.dumps(req.event_payload or {})
        with get_db() as db:
            db.execute(
                "INSERT INTO jobs (id,pipeline_id,rule_id,series_uid,study_uid,"
                "source_type,event_source_id,event_payload,status,trigger_type,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, req.pipeline_id, req.rule_id,
                    "", "",   # series_uid / study_uid empty for non-imaging
                    source_type,
                    req.event_source_id or "",
                    payload_str,
                    "PENDING", req.trigger_type, now,
                ),
            )

    bg.add_task(run_job, job_id)
    return {"job_id": job_id, "status": "PENDING", "pipeline_id": req.pipeline_id,
            "source_type": source_type}


@router.post("", status_code=202)
async def create_job(req: TriggerRequest, bg: BackgroundTasks):
    return await trigger_job(req, bg)


@router.get("")
def list_jobs(pipeline_id: Optional[str] = None, status: Optional[str] = None,
              source_type: Optional[str] = None, limit: int = 200):
    with get_db() as db:
        where, params = [], []
        if pipeline_id:
            where.append("j.pipeline_id=?"); params.append(pipeline_id)
        if status:
            where.append("j.status=?"); params.append(status)
        if source_type:
            where.append("j.source_type=?"); params.append(source_type)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = db.execute(
            f"SELECT j.*, p.name as pipeline_name FROM jobs j "
            f"JOIN pipelines p ON p.id=j.pipeline_id {clause} "
            f"ORDER BY j.created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return rows_to_list(rows)


@router.get("/{job_id}")
def get_job(job_id: str):
    with get_db() as db:
        row = db.execute(
            "SELECT j.*, p.name as pipeline_name FROM jobs j "
            "JOIN pipelines p ON p.id=j.pipeline_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        job = dict(row)
        job["artifacts"] = rows_to_list(
            db.execute("SELECT * FROM artifacts WHERE job_id=? ORDER BY direction,created_at",
                       (job_id,)).fetchall()
        )
        job["saveback_events"] = rows_to_list(
            db.execute("SELECT * FROM saveback_events WHERE job_id=? ORDER BY created_at DESC",
                       (job_id,)).fetchall()
        )
    return job


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: str):
    with get_db() as db:
        db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
