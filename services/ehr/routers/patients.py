import os, httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict, new_id

router = APIRouter(prefix="/api/patients", tags=["patients"])

FHIR_BRIDGE_URL = os.environ.get("FHIR_BRIDGE_URL", "")

class PatientCreate(BaseModel):
    mrn: str
    first_name: str
    last_name: str
    birth_date: Optional[str] = None
    sex: Optional[str] = None
    phone: Optional[str] = None
    insurance_id: Optional[str] = None

class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    birth_date: Optional[str] = None
    sex: Optional[str] = None
    phone: Optional[str] = None
    insurance_id: Optional[str] = None

@router.get("")
def list_patients(q: Optional[str] = Query(None)):
    with get_db() as db:
        if q:
            like = f"%{q}%"
            rows = db.execute(
                "SELECT * FROM patients WHERE first_name LIKE ? OR last_name LIKE ? OR mrn LIKE ? ORDER BY last_name",
                (like, like, like)).fetchall()
        else:
            rows = db.execute("SELECT * FROM patients ORDER BY last_name").fetchall()
        return rows_to_list(rows)

@router.get("/{patient_id}")
def get_patient(patient_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Patient not found")
        p = dict(row)
        p["allergies"]    = rows_to_list(db.execute("SELECT * FROM allergies WHERE patient_id=?", (patient_id,)).fetchall())
        p["diagnoses"]    = rows_to_list(db.execute("SELECT * FROM diagnoses WHERE patient_id=? AND status='active'", (patient_id,)).fetchall())
        p["encounters"]   = rows_to_list(db.execute("SELECT * FROM encounters WHERE patient_id=? ORDER BY admit_date DESC LIMIT 5", (patient_id,)).fetchall())
        p["cdss_alerts"]  = rows_to_list(db.execute("SELECT * FROM cdss_alerts WHERE patient_id=? AND acknowledged=0 ORDER BY created_at DESC", (patient_id,)).fetchall())
        return p

@router.post("", status_code=201)
async def create_patient(body: PatientCreate):
    pid = new_id()
    with get_db() as db:
        if db.execute("SELECT 1 FROM patients WHERE mrn=?", (body.mrn,)).fetchone():
            raise HTTPException(409, f"MRN {body.mrn} already exists")
        db.execute(
            "INSERT INTO patients(id,mrn,first_name,last_name,birth_date,sex,phone,insurance_id) VALUES(?,?,?,?,?,?,?,?)",
            (pid, body.mrn, body.first_name, body.last_name, body.birth_date, body.sex, body.phone, body.insurance_id))
        row = db.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    patient = dict(row)
    # Notify FHIR bridge asynchronously (fire-and-forget)
    if FHIR_BRIDGE_URL:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                await c.post(f"{FHIR_BRIDGE_URL}/api/events/patient-created", json=patient)
        except Exception:
            pass
    return patient

@router.patch("/{patient_id}")
def update_patient(patient_id: str, body: PatientUpdate):
    allowed = {"first_name", "last_name", "birth_date", "sex", "phone", "insurance_id"}
    updates = {k: v for k, v in body.model_dump().items() if v is not None and k in allowed}
    if not updates:
        raise HTTPException(400, "No fields to update")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE patients SET {sets} WHERE id=?", (*updates.values(), patient_id))
        row = db.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Patient not found")
        return dict(row)

@router.delete("/{patient_id}", status_code=204)
def delete_patient(patient_id: str):
    with get_db() as db:
        db.execute("DELETE FROM patients WHERE id=?", (patient_id,))

# ── Allergies ──────────────────────────────────────────────────────────────

class AllergyCreate(BaseModel):
    substance: str
    reaction: Optional[str] = None
    severity: Optional[str] = "mild"

@router.get("/{patient_id}/allergies")
def list_allergies(patient_id: str):
    with get_db() as db:
        return rows_to_list(db.execute("SELECT * FROM allergies WHERE patient_id=?", (patient_id,)).fetchall())

@router.post("/{patient_id}/allergies", status_code=201)
def add_allergy(patient_id: str, body: AllergyCreate):
    with get_db() as db:
        if not db.execute("SELECT 1 FROM patients WHERE id=?", (patient_id,)).fetchone():
            raise HTTPException(404, "Patient not found")
        cur = db.execute(
            "INSERT INTO allergies(patient_id,substance,reaction,severity) VALUES(?,?,?,?)",
            (patient_id, body.substance, body.reaction, body.severity))
        return row_to_dict(db.execute("SELECT * FROM allergies WHERE id=?", (cur.lastrowid,)).fetchone())

@router.delete("/{patient_id}/allergies/{allergy_id}", status_code=204)
def delete_allergy(patient_id: str, allergy_id: int):
    with get_db() as db:
        db.execute("DELETE FROM allergies WHERE id=? AND patient_id=?", (allergy_id, patient_id))

# ── Diagnoses ──────────────────────────────────────────────────────────────

class DiagnosisCreate(BaseModel):
    icd10_code: str
    description: Optional[str] = None
    encounter_id: Optional[int] = None
    status: Optional[str] = "active"

@router.get("/{patient_id}/diagnoses")
def list_diagnoses(patient_id: str):
    with get_db() as db:
        return rows_to_list(db.execute("SELECT * FROM diagnoses WHERE patient_id=? ORDER BY created_at DESC", (patient_id,)).fetchall())

@router.post("/{patient_id}/diagnoses", status_code=201)
def add_diagnosis(patient_id: str, body: DiagnosisCreate):
    with get_db() as db:
        if not db.execute("SELECT 1 FROM patients WHERE id=?", (patient_id,)).fetchone():
            raise HTTPException(404, "Patient not found")
        cur = db.execute(
            "INSERT INTO diagnoses(patient_id,encounter_id,icd10_code,description,status) VALUES(?,?,?,?,?)",
            (patient_id, body.encounter_id, body.icd10_code, body.description, body.status))
        return row_to_dict(db.execute("SELECT * FROM diagnoses WHERE id=?", (cur.lastrowid,)).fetchone())
