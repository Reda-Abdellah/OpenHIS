import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Union, List
from database import get_db, rows_to_list
from jwt_auth import require_roles

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


class PipelineCreate(BaseModel):
    # id becomes a path segment and a docker env value — keep it filename-safe.
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str
    description: str = ""
    docker_image: str
    version: str = "1.0.0"
    source_type: str = "imaging"
    output_types: Union[List[str], str] = '["report"]'
    config_json: str = "{}"
    input_schema: str = "{}"

    @field_validator("output_types", mode="before")
    @classmethod
    def coerce_output_types(cls, v):
        if isinstance(v, list):
            return json.dumps(v)
        return v


@router.get("", dependencies=[Depends(require_roles("admin", "radiologist"))])
def list_pipelines():
    with get_db() as db:
        rows = db.execute("""
            SELECT p.*,
                   (SELECT count(*) FROM rules r WHERE r.pipeline_id=p.id) as rules_count,
                   (SELECT count(*) FROM jobs j WHERE j.pipeline_id=p.id) as jobs_count
            FROM pipelines p ORDER BY p.name
        """).fetchall()
    return rows_to_list(rows)


@router.get("/{pipeline_id}", dependencies=[Depends(require_roles("admin", "radiologist"))])
def get_pipeline(pipeline_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Pipeline not found")
        return dict(row)


@router.post("", status_code=201, dependencies=[Depends(require_roles("admin"))])
def create_pipeline(body: PipelineCreate):
    import sqlite3
    with get_db() as db:
        try:
            db.execute(
                "INSERT INTO pipelines (id,name,description,docker_image,version,"
                "source_type,output_types,config_json,input_schema)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (body.id, body.name, body.description, body.docker_image,
                 body.version, body.source_type, body.output_types,
                 body.config_json, body.input_schema),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Pipeline '{body.id}' already exists")
        row = db.execute("SELECT * FROM pipelines WHERE id=?", (body.id,)).fetchone()
    return dict(row)


@router.patch("/{pipeline_id}", dependencies=[Depends(require_roles("admin"))])
def update_pipeline(pipeline_id: str, body: dict):
    allowed = {"name", "description", "docker_image", "version", "enabled",
               "source_type", "output_types", "config_json", "input_schema"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields provided")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE pipelines SET {sets} WHERE id=?", (*updates.values(), pipeline_id))
    return {"updated": pipeline_id}


@router.delete("/{pipeline_id}", status_code=204,
               dependencies=[Depends(require_roles("admin"))])
def delete_pipeline(pipeline_id: str):
    with get_db() as db:
        db.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,))
