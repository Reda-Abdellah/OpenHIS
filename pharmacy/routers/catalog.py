from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/medications", tags=["catalog"])


class MedCreate(BaseModel):
    name: str
    generic_name: Optional[str] = None
    form: Optional[str] = "tablet"
    strength: Optional[str] = None
    route: Optional[str] = "oral"
    unit: Optional[str] = "mg"
    controlled: Optional[int] = 0
    notes: Optional[str] = None


@router.get("")
def list_meds(q: Optional[str] = None, active: Optional[int] = None):
    clauses, params = [], []
    if q:
        clauses.append("(name LIKE ? OR generic_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if active is not None:
        clauses.append("active = ?"); params.append(active)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT m.*, (SELECT quantity FROM stock WHERE medication_id=m.id LIMIT 1) AS stock_qty "
            f"FROM medications m {where} ORDER BY m.name",
            params
        ).fetchall())


@router.post("", status_code=201)
def create_med(body: MedCreate):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO medications(name,generic_name,form,strength,route,unit,controlled,notes) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (body.name, body.generic_name, body.form, body.strength,
             body.route, body.unit, body.controlled, body.notes)
        )
        db.execute("INSERT INTO stock(medication_id,quantity) VALUES(?,?)", (cur.lastrowid, 0))
        return row_to_dict(db.execute("SELECT * FROM medications WHERE id=?", (cur.lastrowid,)).fetchone())


@router.patch("/{med_id}")
def update_med(med_id: int, body: dict):
    allowed = {"name", "generic_name", "form", "strength", "route", "unit", "controlled", "notes", "active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE medications SET {sets} WHERE id=?", (*updates.values(), med_id))
        return row_to_dict(db.execute("SELECT * FROM medications WHERE id=?", (med_id,)).fetchone())
