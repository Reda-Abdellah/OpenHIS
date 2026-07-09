from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import get_db, rows_to_list
from jwt_auth import require_roles

router = APIRouter(prefix="/api/saveback", tags=["saveback"])


class SavebackRequest(BaseModel):
    job_id: str
    artifact_id: int


@router.post("",
             dependencies=[Depends(require_roles("admin", "radiologist", "clinician"))])
async def manual_saveback(body: SavebackRequest):
    from runner import saveback_artifact
    try:
        orthanc_id = await saveback_artifact(body.job_id, body.artifact_id, trigger_type="MANUAL")
        return {"status": "SUCCESS", "orthanc_instance_id": orthanc_id}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.get("/job/{job_id}",
            dependencies=[Depends(require_roles("admin", "radiologist", "clinician"))])
def list_saveback_events(job_id: str):
    with get_db() as db:
        return rows_to_list(
            db.execute(
                "SELECT * FROM saveback_events WHERE job_id=? ORDER BY created_at DESC",
                (job_id,),
            ).fetchall()
        )
