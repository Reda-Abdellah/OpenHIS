import json, datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict, gen_accession

router = APIRouter(prefix="/api/specimens", tags=["specimens"])

FULL_SQL = """
    SELECT s.*, lp.patient_name, lp.mrn, lp.ehr_patient_id
    FROM specimens s
    JOIN lab_patients lp ON lp.id = s.patient_id
"""

class SpecimenCreate(BaseModel):
    patient_id: int
    specimen_type: str          # blood / urine / tissue / csf / swab
    collected_by: Optional[str] = None
    collection_date: Optional[str] = None

@router.get("")
def list_specimens(status: Optional[str] = None, patient_id: Optional[int] = None):
    clauses, params = [], []
    if status:     clauses.append("s.status=?");     params.append(status)
    if patient_id: clauses.append("s.patient_id=?"); params.append(patient_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(f"{FULL_SQL} {where} ORDER BY s.created_at DESC", params).fetchall())

@router.get("/{specimen_id}")
def get_specimen(specimen_id: int):
    with get_db() as db:
        row = db.execute(f"{FULL_SQL} WHERE s.id=?", (specimen_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Specimen not found")
        s = dict(row)
        s["orders"] = rows_to_list(db.execute(
            "SELECT * FROM lab_orders WHERE specimen_id=?", (specimen_id,)).fetchall())
        return s

@router.post("", status_code=201)
def accession_specimen(body: SpecimenCreate):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        if not db.execute("SELECT 1 FROM lab_patients WHERE id=?", (body.patient_id,)).fetchone():
            raise HTTPException(404, "Lab patient not found")
        acc = gen_accession()
        while db.execute("SELECT 1 FROM specimens WHERE accession=?", (acc,)).fetchone():
            acc = gen_accession()
        custody = json.dumps([{"timestamp": now, "actor": body.collected_by or "system",
                               "action": "collected", "location": "collection"}])
        cur = db.execute(
            "INSERT INTO specimens(accession,patient_id,specimen_type,collection_date,collected_by,custody_log) VALUES(?,?,?,?,?,?)",
            (acc, body.patient_id, body.specimen_type,
             body.collection_date or now[:10], body.collected_by, custody))
        return row_to_dict(db.execute(f"{FULL_SQL} WHERE s.id=?", (cur.lastrowid,)).fetchone())

@router.post("/{specimen_id}/receive")
def receive_specimen(specimen_id: int, body: dict):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        row = db.execute("SELECT custody_log FROM specimens WHERE id=?", (specimen_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Specimen not found")
        log = json.loads(row["custody_log"] or "[]")
        log.append({"timestamp": now, "actor": body.get("received_by", "lab"),
                    "action": "received", "location": body.get("location", "lab")})
        db.execute(
            "UPDATE specimens SET status='received', received_date=?, received_by=?, custody_log=? WHERE id=?",
            (now, body.get("received_by"), json.dumps(log), specimen_id))
    return {"status": "received", "specimen_id": specimen_id}
