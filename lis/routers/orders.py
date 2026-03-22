import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/lab-orders", tags=["lab-orders"])

ORDER_SQL = """
    SELECT lo.*, s.accession, s.specimen_type, s.status AS specimen_status,
           lp.patient_name, lp.mrn, lp.ehr_patient_id
    FROM lab_orders lo
    JOIN specimens s ON s.id = lo.specimen_id
    JOIN lab_patients lp ON lp.id = s.patient_id
"""

TEST_CATALOG = {
    "CBC":   "Complete Blood Count",
    "BMP":   "Basic Metabolic Panel",
    "CMP":   "Comprehensive Metabolic Panel",
    "LFT":   "Liver Function Tests",
    "LIPID": "Lipid Panel",
    "TSH":   "Thyroid Stimulating Hormone",
    "TROPONIN": "Troponin I",
    "UA":    "Urinalysis",
    "URINE_CULTURE": "Urine Culture",
    "BLOOD_CULTURE": "Blood Culture",
    "HBA1C": "HbA1c",
    "COAG":  "Coagulation Screen (PT/APTT/INR)",
}

class LabOrderCreate(BaseModel):
    ehr_order_id: Optional[str] = None
    specimen_id: int
    test_code: str
    priority: Optional[str] = "ROUTINE"
    ordered_by: Optional[str] = None

class LabOrderUpdate(BaseModel):
    status: Optional[str] = None
    instrument_id: Optional[str] = None

@router.get("")
def list_orders(status: Optional[str] = None, specimen_id: Optional[int] = None):
    clauses, params = [], []
    if status:      clauses.append("lo.status=?");      params.append(status)
    if specimen_id: clauses.append("lo.specimen_id=?"); params.append(specimen_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(f"{ORDER_SQL} {where} ORDER BY lo.created_at DESC", params).fetchall())

@router.get("/{order_id}")
def get_order(order_id: int):
    with get_db() as db:
        row = db.execute(f"{ORDER_SQL} WHERE lo.id=?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        o = dict(row)
        o["results"] = rows_to_list(db.execute(
            "SELECT * FROM lab_results WHERE order_id=?", (order_id,)).fetchall())
        return o

@router.post("", status_code=201)
def create_order(body: LabOrderCreate):
    code = body.test_code.upper()
    name = TEST_CATALOG.get(code, code)
    with get_db() as db:
        if not db.execute("SELECT 1 FROM specimens WHERE id=?", (body.specimen_id,)).fetchone():
            raise HTTPException(404, "Specimen not found")
        cur = db.execute(
            "INSERT INTO lab_orders(ehr_order_id,specimen_id,test_code,test_name,priority,ordered_by) VALUES(?,?,?,?,?,?)",
            (body.ehr_order_id, body.specimen_id, code, name, body.priority or "ROUTINE", body.ordered_by))
        return row_to_dict(db.execute(f"{ORDER_SQL} WHERE lo.id=?", (cur.lastrowid,)).fetchone())

@router.patch("/{order_id}")
def update_order(order_id: int, body: LabOrderUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updates["updated_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE lab_orders SET {sets} WHERE id=?", (*updates.values(), order_id))
        return row_to_dict(db.execute(f"{ORDER_SQL} WHERE lo.id=?", (order_id,)).fetchone())

@router.get("/catalog")
def get_catalog():
    return [{"code": k, "name": v} for k, v in TEST_CATALOG.items()]
