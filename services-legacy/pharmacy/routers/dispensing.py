import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/dispenses", tags=["dispensing"])


class DispenseCreate(BaseModel):
    prescription_id: int
    quantity:        int
    dispensed_by:    Optional[str] = None
    lot_number:      Optional[str] = None
    expiry_date:     Optional[str] = None


@router.get("")
def list_dispenses(ehr_patient_id: Optional[str] = None):
    with get_db() as db:
        if ehr_patient_id:
            rows = db.execute(
                "SELECT d.*, p.drug_name, p.dose FROM dispenses d "
                "JOIN prescriptions p ON p.id=d.prescription_id "
                "WHERE d.ehr_patient_id=? ORDER BY d.dispensedat DESC", (ehr_patient_id,)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT d.*, p.drug_name, p.dose FROM dispenses d "
                "JOIN prescriptions p ON p.id=d.prescription_id ORDER BY d.dispensedat DESC LIMIT 200"
            ).fetchall()
    return rows_to_list(rows)


@router.post("", status_code=201)
def dispense(body: DispenseCreate):
    with get_db() as db:
        rx = db.execute(
            "SELECT p.*, m.id AS mid FROM prescriptions p "
            "LEFT JOIN medications m ON m.id=p.medication_id WHERE p.id=?",
            (body.prescription_id,)
        ).fetchone()
        if not rx:
            raise HTTPException(404, "Prescription not found")
        rx = dict(rx)
        if rx["status"] != "verified":
            raise HTTPException(409, f"Prescription must be verified before dispensing (status: {rx['status']})")

        # Decrement stock if medication is linked
        if rx.get("mid"):
            stock = db.execute(
                "SELECT * FROM stock WHERE medication_id=? LIMIT 1", (rx["mid"],)
            ).fetchone()
            if stock and dict(stock)["quantity"] < body.quantity:
                raise HTTPException(409, f"Insufficient stock: {dict(stock)['quantity']} available")
            if stock:
                db.execute(
                    "UPDATE stock SET quantity=quantity-?, updatedat=datetime('now') WHERE id=?",
                    (body.quantity, dict(stock)["id"])
                )

        cur = db.execute(
            "INSERT INTO dispenses(prescription_id,ehr_patient_id,quantity,dispensed_by,lot_number,expiry_date) "
            "VALUES(?,?,?,?,?,?)",
            (body.prescription_id, rx["ehr_patient_id"], body.quantity,
             body.dispensed_by, body.lot_number, body.expiry_date)
        )
        db.execute("UPDATE prescriptions SET status='dispensed' WHERE id=?", (body.prescription_id,))
        return row_to_dict(db.execute("SELECT * FROM dispenses WHERE id=?", (cur.lastrowid,)).fetchone())
