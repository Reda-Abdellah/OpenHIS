from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


class PipelineCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    docker_image: str
    version: str = "1.0.0"
    output_types: str = '["report"]'
    config_json: str = "{}"


@router.get("")
def list_pipelines():
    with get_db() as db:
        rows = db.execute("""
            SELECT p.*,
                   (SELECT count(*) FROM rules r WHERE r.pipeline_id=p.id) as rules_count,
                   (SELECT count(*) FROM jobs j WHERE j.pipeline_id=p.id) as jobs_count
            FROM pipelines p ORDER BY p.name
        """).fetchall()
    return rows_to_list(rows)


@router.get("/{pipeline_id}")
def get_pipeline(pipeline_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Pipeline not found")
        return dict(row)


@router.post("", status_code=201)
def create_pipeline(body: PipelineCreate):
    with get_db() as db:
        db.execute(
            "INSERT INTO pipelines (id,name,description,docker_image,version,output_types,config_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (body.id, body.name, body.description, body.docker_image,
             body.version, body.output_types, body.config_json),
        )
    return {"id": body.id}


@router.patch("/{pipeline_id}")
def update_pipeline(pipeline_id: str, body: dict):
    allowed = {"name", "description", "docker_image", "version", "enabled", "output_types", "config_json"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields provided")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE pipelines SET {sets} WHERE id=?", (*updates.values(), pipeline_id))
    return {"updated": pipeline_id}


@router.delete("/{pipeline_id}", status_code=204)
def delete_pipeline(pipeline_id: str):
    with get_db() as db:
        db.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,))
