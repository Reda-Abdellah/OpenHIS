import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/prescriptions", tags=["prescriptions"])

RX_SQL = """
    SELECT p.*,
           m.name AS medication_name, m.form, m.controlled,
           (SELECT quantity FROM stock WHERE medication_id=m.id LIMIT 1) AS stock_qty
    FROM   prescriptions p
    LEFT JOIN medications m ON m.id = p.medication_id
"""


class RxCreate(BaseModel):
    ehr_order_id:  Optional[str] = None
    ehr_patient_id: str
    drug_name:     str
    medication_id: Optional[int] = None
    dose:          str
    route:         Optional[str] = "oral"
    frequency:     str
    duration_days: Optional[int] = None
    quantity:      Optional[int] = 1
    prescriber:    Optional[str] = None
    notes:         Optional[str] = None


@router.get("")
def list_rx(ehr_patient_id: Optional[str] = None, status: Optional[str] = None):
    clauses, params = [], []
    if ehr_patient_id:
        clauses.append("p.ehr_patient_id=?"); params.append(ehr_patient_id)
    if status:
        clauses.append("p.status=?"); params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(f"{RX_SQL} {where} ORDER BY p.createdat DESC", params).fetchall())


@router.get("/pending-count")
def pending_count():
    with get_db() as db:
        n = db.execute("SELECT COUNT(*) FROM prescriptions WHERE status='pending'").fetchone()[0]
    return {"pending": n}


@router.get("/{rx_id}")
def get_rx(rx_id: int):
    with get_db() as db:
        row = db.execute(f"{RX_SQL} WHERE p.id=?", (rx_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Prescription not found")
        return dict(row)


@router.post("", status_code=201)
def create_rx(body: RxCreate):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO prescriptions"
            "(ehr_order_id,ehr_patient_id,medication_id,drug_name,dose,route,"
            "frequency,duration_days,quantity,prescriber,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (body.ehr_order_id, body.ehr_patient_id, body.medication_id, body.drug_name,
             body.dose, body.route, body.frequency, body.duration_days,
             body.quantity, body.prescriber, body.notes)
        )
        return row_to_dict(db.execute(f"{RX_SQL} WHERE p.id=?", (cur.lastrowid,)).fetchone())


@router.post("/{rx_id}/verify")
def verify_rx(rx_id: int, body: dict):
    pharmacist = body.get("pharmacist", "Pharmacist")
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        row = db.execute("SELECT status FROM prescriptions WHERE id=?", (rx_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Prescription not found")
        if dict(row)["status"] != "pending":
            raise HTTPException(409, f"Cannot verify: status is '{dict(row)['status']}'")
        db.execute(
            "UPDATE prescriptions SET status='verified', verified_by=?, verifiedat=? WHERE id=?",
            (pharmacist, now, rx_id)
        )
        return row_to_dict(db.execute(f"{RX_SQL} WHERE p.id=?", (rx_id,)).fetchone())


@router.post("/{rx_id}/cancel")
def cancel_rx(rx_id: int):
    with get_db() as db:
        row = db.execute("SELECT status FROM prescriptions WHERE id=?", (rx_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Prescription not found")
        if dict(row)["status"] in ("dispensed", "cancelled"):
            raise HTTPException(409, f"Cannot cancel: status is '{dict(row)['status']}'")
        db.execute("UPDATE prescriptions SET status='cancelled' WHERE id=?", (rx_id,))
        return row_to_dict(db.execute(f"{RX_SQL} WHERE p.id=?", (rx_id,)).fetchone())
