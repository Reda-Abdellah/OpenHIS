from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleCreate(BaseModel):
    pipeline_id: str
    name: str
    modality: Optional[str] = None
    body_part: Optional[str] = None
    trigger_filter: str = "{}"
    auto_trigger: int = 0
    auto_saveback: int = 0
    saveback_types: str = '["report"]'
    priority: int = 0
    enabled: int = 1


@router.get("")
def list_rules(pipeline_id: Optional[str] = None):
    with get_db() as db:
        if pipeline_id:
            rows = db.execute(
                "SELECT r.*, p.name as pipeline_name FROM rules r "
                "JOIN pipelines p ON p.id=r.pipeline_id WHERE r.pipeline_id=? ORDER BY r.priority DESC",
                (pipeline_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT r.*, p.name as pipeline_name FROM rules r "
                "JOIN pipelines p ON p.id=r.pipeline_id ORDER BY r.priority DESC"
            ).fetchall()
    return rows_to_list(rows)


@router.get("/{rule_id}")
def get_rule(rule_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Rule not found")
        return dict(row)


@router.post("", status_code=201)
def create_rule(body: RuleCreate):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO rules (pipeline_id,name,modality,body_part,trigger_filter,"
            "auto_trigger,auto_saveback,saveback_types,priority,enabled)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (body.pipeline_id, body.name, body.modality, body.body_part,
             body.trigger_filter, body.auto_trigger, body.auto_saveback,
             body.saveback_types, body.priority, body.enabled),
        )
        row = db.execute("SELECT * FROM rules WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.patch("/{rule_id}")
def update_rule(rule_id: int, body: dict):
    allowed = {"name", "modality", "body_part", "trigger_filter",
               "auto_trigger", "auto_saveback", "saveback_types", "priority", "enabled"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields provided")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE rules SET {sets} WHERE id=?", (*updates.values(), rule_id))
    return {"updated": rule_id}


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int):
    with get_db() as db:
        db.execute("DELETE FROM rules WHERE id=?", (rule_id,))
