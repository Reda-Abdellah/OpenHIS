from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/billing", tags=["billing"])

class BillingCreate(BaseModel):
    patient_id: str
    encounter_id: Optional[int] = None
    cpt_code: str
    description: Optional[str] = None
    amount: float

@router.get("")
def list_billing(patient_id: Optional[str] = None, status: Optional[str] = None):
    clauses, params = [], []
    if patient_id: clauses.append("b.patient_id=?"); params.append(patient_id)
    if status:     clauses.append("b.status=?");      params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT b.*, p.first_name||' '||p.last_name AS patient_name FROM billing_records b "
            f"JOIN patients p ON p.id=b.patient_id {where} ORDER BY b.created_at DESC", params).fetchall())

@router.post("", status_code=201)
def create_record(body: BillingCreate):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO billing_records(patient_id,encounter_id,cpt_code,description,amount) VALUES(?,?,?,?,?)",
            (body.patient_id, body.encounter_id, body.cpt_code, body.description, body.amount))
        return row_to_dict(db.execute("SELECT * FROM billing_records WHERE id=?", (cur.lastrowid,)).fetchone())

@router.patch("/{record_id}/status")
def update_status(record_id: int, body: dict):
    status = body.get("status")
    if not status:
        raise HTTPException(400, "status required")
    with get_db() as db:
        db.execute("UPDATE billing_records SET status=? WHERE id=?", (status, record_id))
    return {"updated": record_id, "status": status}
