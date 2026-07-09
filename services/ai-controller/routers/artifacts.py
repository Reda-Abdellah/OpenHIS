import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from database import get_db, rows_to_list
from jwt_auth import require_roles

# Router-level gate: every artifacts route (list / get / download) serves PHI.
router = APIRouter(
    prefix="/api/artifacts",
    tags=["artifacts"],
    dependencies=[Depends(require_roles("admin", "radiologist", "clinician"))],
)

JOBS_DATA_DIR = os.environ.get("JOBS_DATA_DIR", "/data/jobs")


@router.get("/job/{job_id}")
def list_artifacts(job_id: str):
    with get_db() as db:
        return rows_to_list(
            db.execute(
                "SELECT * FROM artifacts WHERE job_id=? ORDER BY direction, created_at",
                (job_id,),
            ).fetchall()
        )


@router.get("/{artifact_id}")
def get_artifact(artifact_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Artifact not found")
        return dict(row)


@router.get("/{artifact_id}/download")
def download_artifact(artifact_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Artifact not found")
        art = dict(row)
    file_path = os.path.join(JOBS_DATA_DIR, art["rel_path"])
    if not os.path.exists(file_path):
        raise HTTPException(404, "Artifact file not found on disk")
    return FileResponse(path=file_path, filename=art["filename"])
