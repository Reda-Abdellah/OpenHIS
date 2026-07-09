from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict
from openhis_sdk.auth import require_roles

router = APIRouter(prefix="/api/patients", tags=["patients"])


class PatientCreate(BaseModel):
    orthanc_id:   Optional[str] = None
    mrn:          Optional[str] = None
    patient_name: str
    birth_date:   Optional[str] = None
    sex:          Optional[str] = None


class PatientUpdate(BaseModel):
    patient_name: Optional[str] = None
    birth_date:   Optional[str] = None
    sex:          Optional[str] = None


@router.get("", dependencies=[Depends(require_roles("clinician", "radiologist", "lab-tech", "admin"))])
def list_patients(q: Optional[str] = Query(None)):
    with get_db() as db:
        if q:
            like = f"%{q}%"
            rows = db.execute(
                "SELECT * FROM patients WHERE patient_name LIKE ? OR mrn LIKE ?"
                " ORDER BY patient_name",
                (like, like)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM patients ORDER BY patient_name").fetchall()
        return rows_to_list(rows)


@router.get("/{patient_id}", dependencies=[Depends(require_roles("clinician", "radiologist", "lab-tech", "admin"))])
def get_patient(patient_id: int):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Patient not found")
        return dict(row)


@router.post("", status_code=201, dependencies=[Depends(require_roles("clinician", "admin"))])
def create_patient(body: PatientCreate):
    with get_db() as db:
        if body.mrn and db.execute(
                "SELECT 1 FROM patients WHERE mrn=?",
                (body.mrn,)).fetchone():
            raise HTTPException(409, f"MRN {body.mrn} already exists")
        cur = db.execute(
            "INSERT INTO patients(orthanc_id,mrn,patient_name,birth_date,sex)"
            " VALUES(?,?,?,?,?)",
            (body.orthanc_id, body.mrn, body.patient_name,
             body.birth_date, body.sex))
        return row_to_dict(db.execute(
            "SELECT * FROM patients WHERE id=?", (cur.lastrowid,)).fetchone())


@router.patch("/{patient_id}", dependencies=[Depends(require_roles("clinician", "admin"))])
def update_patient(patient_id: int, body: PatientUpdate):
    allowed = {"patient_name", "birth_date", "sex"}
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


@router.delete("/{patient_id}", status_code=204, dependencies=[Depends(require_roles("admin"))])
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


@router.post("/from-ehr", status_code=200, dependencies=[Depends(require_roles("clinician", "admin"))])
def upsert_from_ehr(body: EHRPatientPush):
    """
    Upsert a patient pushed by the EHR via the FHIR bridge.
    Matches on MRN; creates or updates the record.
    """
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM patients WHERE mrn=?",
            (body.mrn,)).fetchone()
        if existing:
            db.execute(
                "UPDATE patients SET patient_name=?, birth_date=?, sex=?"
                " WHERE mrn=?",
                (body.patient_name, body.birth_date, body.sex, body.mrn))
            return {"action": "updated", "mrn": body.mrn}
        db.execute(
            "INSERT INTO patients(orthanc_id,mrn,patient_name,birth_date,sex)"
            " VALUES(?,?,?,?,?)",
            (body.ehr_id or body.mrn, body.mrn,
             body.patient_name, body.birth_date, body.sex))
        return {"action": "created", "mrn": body.mrn}
