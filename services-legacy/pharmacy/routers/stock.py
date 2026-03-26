from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/stock", tags=["stock"])


class StockAdjust(BaseModel):
    quantity_delta: int       # positive = restock, negative = manual removal
    lot_number:     Optional[str] = None
    expiry_date:    Optional[str] = None
    location:       Optional[str] = None
    low_threshold:  Optional[int] = None


@router.get("")
def list_stock(low_only: bool = False):
    with get_db() as db:
        rows = db.execute(
            "SELECT s.*, m.name, m.generic_name, m.form, m.strength, m.controlled "
            "FROM stock s JOIN medications m ON m.id=s.medication_id "
            "ORDER BY m.name"
        ).fetchall()
    result = rows_to_list(rows)
    if low_only:
        result = [r for r in result if r["quantity"] <= r["low_threshold"]]
    return result


@router.get("/alerts")
def stock_alerts():
    with get_db() as db:
        rows = db.execute(
            "SELECT s.*, m.name FROM stock s JOIN medications m ON m.id=s.medication_id "
            "WHERE s.quantity <= s.low_threshold ORDER BY s.quantity"
        ).fetchall()
    return rows_to_list(rows)


@router.patch("/{stock_id}")
def adjust_stock(stock_id: int, body: StockAdjust):
    with get_db() as db:
        row = db.execute("SELECT * FROM stock WHERE id=?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Stock record not found")
        new_qty = dict(row)["quantity"] + body.quantity_delta
        if new_qty < 0:
            raise HTTPException(409, f"Adjustment would result in negative stock ({new_qty})")
        extras = {}
        if body.lot_number:    extras["lot_number"]    = body.lot_number
        if body.expiry_date:   extras["expiry_date"]   = body.expiry_date
        if body.location:      extras["location"]      = body.location
        if body.low_threshold: extras["low_threshold"] = body.low_threshold
        extras["quantity"]  = new_qty
        extras["updatedat"] = "datetime('now')"
        sets = ", ".join(f"{k}=?" if k != "updatedat" else f"{k}={v}"
                         for k, v in extras.items())
        vals = [v for k, v in extras.items() if k != "updatedat"]
        db.execute(f"UPDATE stock SET {sets} WHERE id=?", (*vals, stock_id))
        return row_to_dict(db.execute("SELECT * FROM stock WHERE id=?", (stock_id,)).fetchone())
