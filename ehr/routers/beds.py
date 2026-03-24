from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/beds", tags=["beds"])

_SQL = """
    SELECT  b.*,
            e.id                             AS encounter_id,
            e.admit_date                     AS admit_date,
            e.attending_physician            AS attending_physician,
            p.id                             AS patient_id,
            p.first_name || ' ' || p.last_name AS patient_name,
            p.mrn, p.birth_date, p.sex
    FROM    beds b
    LEFT JOIN encounters e
           ON  e.ward      = b.ward
           AND e.bed       = b.bed_label
           AND e.status    = 'active'
    LEFT JOIN patients p ON p.id = e.patient_id
"""


class BedCreate(BaseModel):
    ward: str
    room: Optional[str] = None
    bed_label: str
    bed_type: Optional[str] = "standard"   # standard | icu | isolation
    notes: Optional[str] = None


class BedUpdate(BaseModel):
    status: Optional[str] = None   # available | occupied | housekeeping | maintenance
    notes: Optional[str] = None
    bed_type: Optional[str] = None


@router.get("")
def list_beds(ward: Optional[str] = None, status: Optional[str] = None):
    clauses, params = [], []
    if ward:
        clauses.append("b.ward = ?");   params.append(ward)
    if status:
        clauses.append("b.status = ?"); params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"{_SQL} {where} ORDER BY b.ward, b.room, b.bed_label", params
        ).fetchall())


@router.get("/board")
def get_board():
    """Real-time board grouped by ward."""
    with get_db() as db:
        beds = rows_to_list(db.execute(
            f"{_SQL} ORDER BY b.ward, b.room, b.bed_label"
        ).fetchall())
    board: dict = {}
    for bed in beds:
        w = bed["ward"]
        if w not in board:
            board[w] = {
                "ward": w, "beds": [], "total": 0,
                "occupied": 0, "available": 0,
                "housekeeping": 0, "maintenance": 0,
            }
        board[w]["beds"].append(bed)
        board[w]["total"] += 1
        s = bed.get("status", "available")
        if s in board[w]:
            board[w][s] += 1
    return list(board.values())


@router.get("/stats")
def get_stats():
    with get_db() as db:
        return rows_to_list(db.execute(
            "SELECT status, COUNT(*) AS count FROM beds GROUP BY status"
        ).fetchall())


@router.get("/{bed_id}")
def get_bed(bed_id: int):
    with get_db() as db:
        row = db.execute(f"{_SQL} WHERE b.id = ?", (bed_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Bed not found")
        return dict(row)


@router.post("", status_code=201)
def create_bed(body: BedCreate):
    with get_db() as db:
        if db.execute(
            "SELECT 1 FROM beds WHERE ward = ? AND bed_label = ?",
            (body.ward, body.bed_label)
        ).fetchone():
            raise HTTPException(409, f"Bed {body.bed_label} already exists in ward {body.ward}")
        cur = db.execute(
            "INSERT INTO beds(ward,room,bed_label,bed_type,notes) VALUES(?,?,?,?,?)",
            (body.ward, body.room, body.bed_label, body.bed_type, body.notes)
        )
        return row_to_dict(db.execute("SELECT * FROM beds WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.patch("/{bed_id}")
def update_bed(bed_id: int, body: BedUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No valid fields provided")
    sets = ", ".join(f"{k} = ?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE beds SET {sets} WHERE id = ?", (*updates.values(), bed_id))
        row = db.execute("SELECT * FROM beds WHERE id = ?", (bed_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Bed not found")
        return dict(row)


@router.delete("/{bed_id}", status_code=204)
def delete_bed(bed_id: int):
    with get_db() as db:
        db.execute("DELETE FROM beds WHERE id = ?", (bed_id,))
