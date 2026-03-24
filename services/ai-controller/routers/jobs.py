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
    orthanc_series_id: str
    rule_id: Optional[int] = None
    trigger_type: str = "MANUAL"


async def trigger_job(req: TriggerRequest, bg: BackgroundTasks) -> dict:
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

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,rule_id,series_uid,study_uid,"
            "patient_name,patient_id,modality,body_part,accession_number,"
            "orthanc_series_id,orthanc_study_id,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                "PENDING", req.trigger_type, now,
            ),
        )
    bg.add_task(run_job, job_id)
    return {"job_id": job_id, "status": "PENDING", "pipeline_id": req.pipeline_id}


@router.post("", status_code=202)
async def create_job(req: TriggerRequest, bg: BackgroundTasks):
    return await trigger_job(req, bg)


@router.get("")
def list_jobs(pipeline_id: Optional[str] = None, status: Optional[str] = None, limit: int = 200):
    with get_db() as db:
        where, params = [], []
        if pipeline_id:
            where.append("j.pipeline_id=?"); params.append(pipeline_id)
        if status:
            where.append("j.status=?"); params.append(status)
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
