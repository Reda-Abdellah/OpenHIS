import os
import httpx
import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict


FHIR_BRIDGE_URL = os.environ.get('FHIR_BRIDGE_URL', '')


async def _notify_encounter(event: str, encounter: dict):
    if not FHIR_BRIDGE_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            await c.post(f"{FHIR_BRIDGE_URL}/api/events/{event}", json=encounter)
    except Exception:
        pass

router = APIRouter(prefix="/api/encounters", tags=["encounters"])

class EncounterCreate(BaseModel):
    patient_id: str
    encounter_type: Optional[str] = "outpatient"
    ward: Optional[str] = None
    bed: Optional[str] = None
    attending_physician: Optional[str] = None

class EncounterUpdate(BaseModel):
    discharge_date: Optional[str] = None
    ward: Optional[str] = None
    bed: Optional[str] = None
    attending_physician: Optional[str] = None
    status: Optional[str] = None

FULL_SQL = """
    SELECT e.*, p.first_name || ' ' || p.last_name AS patient_name, p.mrn
    FROM encounters e
    JOIN patients p ON p.id = e.patient_id
"""

@router.get("")
def list_encounters(patient_id: Optional[str] = None, status: Optional[str] = None):
    clauses, params = [], []
    if patient_id:
        clauses.append("e.patient_id=?"); params.append(patient_id)
    if status:
        clauses.append("e.status=?"); params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(f"{FULL_SQL} {where} ORDER BY e.admit_date DESC", params).fetchall())

@router.get("/{encounter_id}")
def get_encounter(encounter_id: int):
    with get_db() as db:
        row = db.execute(f"{FULL_SQL} WHERE e.id=?", (encounter_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Encounter not found")
        return dict(row)

@router.post("", status_code=201)
async def admit_patient(body: EncounterCreate, bg: BackgroundTasks):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        if not db.execute("SELECT 1 FROM patients WHERE id=?", (body.patient_id,)).fetchone():
            raise HTTPException(404, "Patient not found")
        if body.ward and body.bed:
            bed = db.execute(
                "SELECT status FROM beds WHERE ward=? AND bed_label=?",
                (body.ward, body.bed)
            ).fetchone()
            if bed and bed["status"] in ("occupied", "housekeeping"):
                raise HTTPException(409, f"Bed {body.bed} in {body.ward} is not available")
        cur = db.execute(
            "INSERT INTO encounters(patient_id,encounter_type,admit_date,ward,bed,attending_physician) VALUES(?,?,?,?,?,?)",
            (body.patient_id, body.encounter_type, now, body.ward, body.bed, body.attending_physician))
        if body.ward and body.bed:
            db.execute(
                "UPDATE beds SET status='occupied' WHERE ward=? AND bed_label=?",
                (body.ward, body.bed)
            )
        return row_to_dict(db.execute(f"{FULL_SQL} WHERE e.id=?", (cur.lastrowid,)).fetchone())

@router.patch("/{encounter_id}")
def update_encounter(encounter_id: int, body: EncounterUpdate):
    allowed = {"discharge_date", "ward", "bed", "attending_physician", "status"}
    updates = {k: v for k, v in body.model_dump().items() if v is not None and k in allowed}
    if not updates:
        raise HTTPException(400, "No fields to update")
    # Auto-set discharge_date on discharge
    if updates.get("status") == "discharged" and "discharge_date" not in updates:
        updates["discharge_date"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE encounters SET {sets} WHERE id=?", (*updates.values(), encounter_id))
        if updates.get("status") == "discharged":
            enc = db.execute("SELECT ward, bed FROM encounters WHERE id=?", (encounter_id,)).fetchone()
            if enc and enc["ward"] and enc["bed"]:
                db.execute(
                    "UPDATE beds SET status='housekeeping' WHERE ward=? AND bed_label=?",
                    (enc["ward"], enc["bed"])
                )
        return row_to_dict(db.execute(f"{FULL_SQL} WHERE e.id=?", (encounter_id,)).fetchone())
