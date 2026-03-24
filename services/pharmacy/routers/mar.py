from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/mar", tags=["mar"])


class MarCreate(BaseModel):
    prescription_id: int
    administered_by: Optional[str] = None
    dose_given:      Optional[str] = None
    route:           Optional[str] = None
    status:          Optional[str] = "given"   # given | held | refused | missed
    notes:           Optional[str] = None


@router.get("")
def list_mar(ehr_patient_id: Optional[str] = None, prescription_id: Optional[int] = None):
    clauses, params = [], []
    if ehr_patient_id:
        clauses.append("m.ehr_patient_id=?"); params.append(ehr_patient_id)
    if prescription_id:
        clauses.append("m.prescription_id=?"); params.append(prescription_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT m.*, p.drug_name, p.dose, p.frequency FROM mar_records m "
            f"JOIN prescriptions p ON p.id=m.prescription_id {where} ORDER BY m.administeredat DESC",
            params
        ).fetchall())


@router.post("", status_code=201)
def record_administration(body: MarCreate):
    with get_db() as db:
        rx = db.execute("SELECT * FROM prescriptions WHERE id=?", (body.prescription_id,)).fetchone()
        if not rx:
            raise HTTPException(404, "Prescription not found")
        rx = dict(rx)
        cur = db.execute(
            "INSERT INTO mar_records(prescription_id,ehr_patient_id,administered_by,"
            "dose_given,route,status,notes) VALUES(?,?,?,?,?,?,?)",
            (body.prescription_id, rx["ehr_patient_id"], body.administered_by,
             body.dose_given or rx["dose"], body.route or rx["route"],
             body.status, body.notes)
        )
        return row_to_dict(db.execute("SELECT * FROM mar_records WHERE id=?", (cur.lastrowid,)).fetchone())
