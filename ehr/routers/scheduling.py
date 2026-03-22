from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/appointments", tags=["scheduling"])

class AppointmentCreate(BaseModel):
    patient_id: str
    provider: Optional[str] = None
    department: Optional[str] = None
    scheduled_date: str
    duration_minutes: Optional[int] = 30
    notes: Optional[str] = None

@router.get("")
def list_appointments(patient_id: Optional[str] = None, status: Optional[str] = None):
    clauses, params = [], []
    if patient_id: clauses.append("a.patient_id=?"); params.append(patient_id)
    if status:     clauses.append("a.status=?");      params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT a.*, p.first_name||' '||p.last_name AS patient_name FROM appointments a "
            f"JOIN patients p ON p.id=a.patient_id {where} ORDER BY a.scheduled_date", params).fetchall())

@router.post("", status_code=201)
def create_appointment(body: AppointmentCreate):
    with get_db() as db:
        if not db.execute("SELECT 1 FROM patients WHERE id=?", (body.patient_id,)).fetchone():
            raise HTTPException(404, "Patient not found")
        cur = db.execute(
            "INSERT INTO appointments(patient_id,provider,department,scheduled_date,duration_minutes,notes) VALUES(?,?,?,?,?,?)",
            (body.patient_id, body.provider, body.department, body.scheduled_date, body.duration_minutes, body.notes))
        return row_to_dict(db.execute("SELECT * FROM appointments WHERE id=?", (cur.lastrowid,)).fetchone())

@router.patch("/{appt_id}")
def update_appointment(appt_id: int, body: dict):
    allowed = {"status", "scheduled_date", "provider", "department", "notes"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE appointments SET {sets} WHERE id=?", (*updates.values(), appt_id))
        return row_to_dict(db.execute("SELECT * FROM appointments WHERE id=?", (appt_id,)).fetchone())

@router.delete("/{appt_id}", status_code=204)
def cancel_appointment(appt_id: int):
    with get_db() as db:
        db.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (appt_id,))
