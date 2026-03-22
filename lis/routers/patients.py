from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/lab-patients", tags=["lab-patients"])

class LabPatientUpsert(BaseModel):
    ehr_patient_id: Optional[str] = None
    patient_name: str
    patient_dob: Optional[str] = None
    mrn: str

@router.get("")
def list_patients(q: Optional[str] = None):
    with get_db() as db:
        if q:
            like = f"%{q}%"
            rows = db.execute(
                "SELECT * FROM lab_patients WHERE patient_name LIKE ? OR mrn LIKE ?",
                (like, like)).fetchall()
        else:
            rows = db.execute("SELECT * FROM lab_patients ORDER BY patient_name").fetchall()
        return rows_to_list(rows)

@router.post("", status_code=201)
def upsert_patient(body: LabPatientUpsert):
    with get_db() as db:
        existing = db.execute("SELECT id FROM lab_patients WHERE mrn=?", (body.mrn,)).fetchone()
        if existing:
            db.execute(
                "UPDATE lab_patients SET ehr_patient_id=?,patient_name=?,patient_dob=? WHERE mrn=?",
                (body.ehr_patient_id, body.patient_name, body.patient_dob, body.mrn))
            row = db.execute("SELECT * FROM lab_patients WHERE mrn=?", (body.mrn,)).fetchone()
        else:
            cur = db.execute(
                "INSERT INTO lab_patients(ehr_patient_id,patient_name,patient_dob,mrn) VALUES(?,?,?,?)",
                (body.ehr_patient_id, body.patient_name, body.patient_dob, body.mrn))
            row = db.execute("SELECT * FROM lab_patients WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
