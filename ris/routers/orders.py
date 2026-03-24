import datetime, random
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api", tags=["orders"])

# ── models ────────────────────────────────────────────────────────────────────

class OrderCreate(BaseModel):
    patient_id           : int
    modality             : str
    body_part            : Optional[str]   = None
    priority             : Optional[str]   = "ROUTINE"
    status               : Optional[str]   = "PENDING"
    requesting_physician : Optional[str]   = None
    clinical_info        : Optional[str]   = None
    scheduled_date       : Optional[str]   = None
    accession_number     : Optional[str]   = None
    orthanc_study_id     : Optional[str]   = None


class OrderUpdate(BaseModel):
    modality             : Optional[str] = None
    body_part            : Optional[str] = None
    priority             : Optional[str] = None
    status               : Optional[str] = None
    requesting_physician : Optional[str] = None
    clinical_info        : Optional[str] = None
    scheduled_date       : Optional[str] = None
    orthanc_study_id     : Optional[str] = None


# ── helpers ───────────────────────────────────────────────────────────────────

def _gen_accession():
    d = datetime.date.today().strftime("%Y%m%d")
    n = random.randint(1000, 9999)
    return f"ACC-{d}-{n}"

_WORKLIST_SQL = """SELECT
    o.id,
    o.accession_number,
    o.modality,
    o.body_part,
    o.priority,
    o.status,
    o.requesting_physician,
    o.clinical_info,
    o.scheduled_date,
    o.orthanc_study_id,
    o.created_at,
    o.updated_at,
    p.patient_name,
    p.mrn,
    p.birth_date,
    p.sex,
    r.status       AS report_status,
    r.id           AS report_id,
    r.updated_at   AS report_updated
FROM orders o
JOIN  patients p ON p.id      = o.patient_id
LEFT JOIN reports r ON r.order_id = o.id
"""

_ORDER_SQL = """    ORDER BY
        CASE o.priority WHEN 'STAT' THEN 0 WHEN 'URGENT' THEN 1 ELSE 2 END,
        CASE o.status   WHEN 'PENDING' THEN 0 WHEN 'IN_PROGRESS' THEN 1 ELSE 2 END,
        o.scheduled_date ASC,
        o.created_at DESC
"""


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/worklist")
def get_worklist(status: str = "", priority: str = "", modality: str = ""):
    clauses, params = [], []
    if status:   clauses.append("o.status = ?");   params.append(status)
    if priority: clauses.append("o.priority = ?"); params.append(priority)
    if modality: clauses.append("o.modality = ?"); params.append(modality)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql   = _WORKLIST_SQL + where + _ORDER_SQL

    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
    return rows_to_list(rows)


@router.get("/orders")
def list_orders(status: str = "", modality: str = ""):
    clauses, params = [], []
    if status:   clauses.append("o.status = ?");   params.append(status)
    if modality: clauses.append("o.modality = ?"); params.append(modality)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(_WORKLIST_SQL + where + _ORDER_SQL, params).fetchall())


@router.patch("/orders/{order_id}")
def patch_order(order_id: int, body: OrderUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    set_clause = ", ".join(f"{k}=?" for k in updates) + ", updated_at=datetime('now')"
    with get_db() as db:
        db.execute(f"UPDATE orders SET {set_clause} WHERE id=?", (*updates.values(), order_id))
        row = db.execute(_WORKLIST_SQL + "WHERE o.id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Order not found")
    return row_to_dict(row)


@router.post("/orders", status_code=201)
def create_order(body: OrderCreate):
    # verify patient exists
    with get_db() as db:
        pt = db.execute("SELECT id FROM patients WHERE id=?",
                        (body.patient_id,)).fetchone()
        if not pt:
            raise HTTPException(404, f"Patient {body.patient_id} not found")

        acc = body.accession_number or _gen_accession()
        # ensure unique
        while db.execute("SELECT 1 FROM orders WHERE accession_number=?",
                         (acc,)).fetchone():
            acc = _gen_accession()

        cur = db.execute(
            """INSERT INTO orders
               (accession_number, patient_id, modality, body_part, priority,
                status, requesting_physician, clinical_info,
                scheduled_date, orthanc_study_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (acc, body.patient_id, body.modality.upper(),
             (body.body_part or "").upper() or None,
             body.priority or "ROUTINE",
             body.status or "PENDING",
             body.requesting_physician, body.clinical_info,
             body.scheduled_date, body.orthanc_study_id),
        )
        oid = cur.lastrowid
        row = db.execute(_WORKLIST_SQL + "WHERE o.id=?", (oid,)).fetchone()
    return row_to_dict(row)


@router.get("/orders/{order_id}")
def get_order(order_id: int):
    with get_db() as db:
        row = db.execute(_WORKLIST_SQL + "WHERE o.id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Order not found")
    return row_to_dict(row)


@router.put("/orders/{order_id}")
def update_order(order_id: int, body: OrderUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    set_clause = ", ".join(f"{k}=?" for k in updates)
    set_clause += ", updated_at=datetime('now')"
    params     = list(updates.values()) + [order_id]

    with get_db() as db:
        db.execute(
            f"UPDATE orders SET {set_clause} WHERE id=?", params
        )
        row = db.execute(_WORKLIST_SQL + "WHERE o.id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Order not found")
    return row_to_dict(row)


@router.delete("/orders/{order_id}", status_code=204)
def cancel_order(order_id: int):
    with get_db() as db:
        row = db.execute("SELECT id FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        db.execute(
            "UPDATE orders SET status='CANCELLED',updated_at=datetime('now') WHERE id=?",
            (order_id,)
        )
