from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/patients", tags=["patients"])


class PatientCreate(BaseModel):
    orthancid:   Optional[str] = None
    patientid:   str                    # MRN
    patientname: str
    birthdate:   Optional[str] = None
    sex:         Optional[str] = None


class PatientUpdate(BaseModel):
    patientname: Optional[str] = None
    birthdate:   Optional[str] = None
    sex:         Optional[str] = None


@router.get("")
def list_patients(q: Optional[str] = Query(None)):
    with get_db() as db:
        if q:
            like = f"%{q}%"
            rows = db.execute(
                "SELECT * FROM patients WHERE patientname LIKE ? OR patientid LIKE ?"
                " ORDER BY patientname",
                (like, like)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM patients ORDER BY patientname").fetchall()
        return rows_to_list(rows)


@router.get("/{patient_id}")
def get_patient(patient_id: int):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Patient not found")
        return dict(row)


@router.post("", status_code=201)
def create_patient(body: PatientCreate):
    with get_db() as db:
        if db.execute(
                "SELECT 1 FROM patients WHERE patientid=?",
                (body.patientid,)).fetchone():
            raise HTTPException(409, f"MRN {body.patientid} already exists")
        cur = db.execute(
            "INSERT INTO patients(orthancid,patientid,patientname,birthdate,sex)"
            " VALUES(?,?,?,?,?)",
            (body.orthancid, body.patientid, body.patientname,
             body.birthdate, body.sex))
        return row_to_dict(db.execute(
            "SELECT * FROM patients WHERE id=?", (cur.lastrowid,)).fetchone())


@router.patch("/{patient_id}")
def update_patient(patient_id: int, body: PatientUpdate):
    allowed = {"patientname", "birthdate", "sex"}
    updates = {k: v for k, v in body.model_dump().items()
               if v is not None and k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE patients SET {sets} WHERE id=?",
                   (*updates.values(), patient_id))
        row = db.execute(
            "SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Patient not found")
        return dict(row)


@router.delete("/{patient_id}", status_code=204)
def delete_patient(patient_id: int):
    with get_db() as db:
        db.execute("DELETE FROM patients WHERE id=?", (patient_id,))


# ── NEW: receive patient pushed by EHR via FHIR bridge ───────────────────────

class EHRPatientPush(BaseModel):
    ehr_id:       Optional[str] = None
    mrn:          str
    patient_name: str
    birth_date:   Optional[str] = None
    sex:          Optional[str] = None


@router.post("/from-ehr", status_code=200)
def upsert_from_ehr(body: EHRPatientPush):
    """
    Upsert a patient pushed by the EHR via the FHIR bridge.
    Matches on MRN (patientid); creates or updates the record.
    """
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM patients WHERE patientid=?",
            (body.mrn,)).fetchone()
        if existing:
            db.execute(
                "UPDATE patients SET patientname=?, birthdate=?, sex=?"
                " WHERE patientid=?",
                (body.patient_name, body.birth_date, body.sex, body.mrn))
            return {"action": "updated", "mrn": body.mrn}
        db.execute(
            "INSERT INTO patients(orthancid,patientid,patientname,birthdate,sex)"
            " VALUES(?,?,?,?,?)",
            (body.ehr_id or body.mrn, body.mrn,
             body.patient_name, body.birth_date, body.sex))
        return {"action": "created", "mrn": body.mrn}
