import json, datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict, gen_accession

router = APIRouter(prefix="/api/specimens", tags=["specimens"])

SPECIMEN_SQL = """
    SELECT s.*, p.patient_name, p.mrn
    FROM specimens s
    JOIN lab_patients p ON p.id = s.patient_id
"""

class SpecimenCreate(BaseModel):
    patient_id:      int
    specimen_type:   str = "blood"
    collected_by:    Optional[str] = None
    collection_date: Optional[str] = None

class ReceiveBody(BaseModel):
    received_by: str
    location:    Optional[str] = "lab"

@router.get("")
def list_specimens(patient_id: Optional[int] = None, status: Optional[str] = None):
    clauses, params = [], []
    if patient_id: clauses.append("s.patient_id=?");  params.append(patient_id)
    if status:     clauses.append("s.status=?");       params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"{SPECIMEN_SQL} {where} ORDER BY s.created_at DESC", params
        ).fetchall())

@router.get("/{specimen_id}")
def get_specimen(specimen_id: int):
    with get_db() as db:
        row = db.execute(f"{SPECIMEN_SQL} WHERE s.id=?", (specimen_id,)).fetchone()
        if not row: raise HTTPException(404, "Specimen not found")
        return row_to_dict(row)

@router.post("", status_code=201)
def create_specimen(body: SpecimenCreate):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        if not db.execute(
            "SELECT 1 FROM lab_patients WHERE id=?", (body.patient_id,)
        ).fetchone():
            raise HTTPException(404, f"Lab patient {body.patient_id} not found")

        acc = gen_accession()
        while db.execute(
            "SELECT 1 FROM specimens WHERE accession_number=?", (acc,)
        ).fetchone():
            acc = gen_accession()

        custody = json.dumps([{
            "timestamp": now, "actor": body.collected_by or "system",
            "action": "collected", "location": "collection"
        }])
        cur = db.execute(
            "INSERT INTO specimens"
            "(accession_number, patient_id, specimen_type, collection_date, collected_by, custody_log)"
            " VALUES(?,?,?,?,?,?)",
            (acc, body.patient_id, body.specimen_type,
             body.collection_date or now[:10], body.collected_by, custody)
        )
        row = db.execute(f"{SPECIMEN_SQL} WHERE s.id=?", (cur.lastrowid,)).fetchone()
        return row_to_dict(row)

@router.patch("/{specimen_id}/receive", status_code=200)
def receive_specimen(specimen_id: int, body: ReceiveBody):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        row = db.execute(
            "SELECT custody_log FROM specimens WHERE id=?", (specimen_id,)
        ).fetchone()
        if not row: raise HTTPException(404, "Specimen not found")
        log = json.loads(row["custody_log"] or "[]")
        log.append({"timestamp": now, "actor": body.received_by,
                    "action": "received", "location": body.location})
        db.execute(
            "UPDATE specimens SET status='received', received_date=?, "
            "received_by=?, custody_log=? WHERE id=?",
            (now, body.received_by, json.dumps(log), specimen_id)
        )
        return row_to_dict(db.execute(f"{SPECIMEN_SQL} WHERE s.id=?", (specimen_id,)).fetchone())
