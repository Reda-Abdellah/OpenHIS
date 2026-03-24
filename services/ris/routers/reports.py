import datetime
import httpx as _httpx

import os
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/reports", tags=["reports"])

VALID_STATUSES = {"DRAFT", "PRELIMINARY", "FINAL", "ADDENDUM"}

_LIST_SQL = """SELECT r.id, r.order_id, r.status AS report_status, r.radiologist,
       r.updated_at AS report_updated, o.accession_number, o.modality,
       p.patient_name
FROM reports r
JOIN orders   o ON o.id = r.order_id
JOIN patients p ON p.id = o.patient_id
"""

_FULL_SQL = """SELECT r.*, o.accession_number, o.modality, o.body_part, o.priority,
       o.status AS order_status, o.clinical_info, o.requesting_physician,
       o.scheduled_date, p.patient_name, p.mrn,
       p.birth_date, p.sex
FROM reports r
JOIN orders   o ON o.id = r.order_id
JOIN patients p ON p.id = o.patient_id
"""

class ReportCreate(BaseModel):
    order_id       : int
    radiologist    : Optional[str] = None
    technique      : Optional[str] = None
    findings       : Optional[str] = None
    impression     : Optional[str] = None
    recommendation : Optional[str] = None
    status         : Optional[str] = "DRAFT"

class ReportUpdate(BaseModel):
    radiologist    : Optional[str] = None
    technique      : Optional[str] = None
    findings       : Optional[str] = None
    impression     : Optional[str] = None
    recommendation : Optional[str] = None
    status         : Optional[str] = None


@router.get("")
def list_reports(status: str = ""):
    sql, params = _LIST_SQL, []
    if status:
        sql += " WHERE r.status=?"; params = [status]
    sql += " ORDER BY r.updated_at DESC"
    with get_db() as db:
        return rows_to_list(db.execute(sql, params).fetchall())


@router.get("/order/{order_id}")
def get_by_order(order_id: int):
    with get_db() as db:
        row = db.execute(_FULL_SQL + "WHERE r.order_id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "No report for this order yet")
    return row_to_dict(row)


@router.post("", status_code=201)
def create_report(body: ReportCreate):
    if body.status and body.status not in VALID_STATUSES:
        raise HTTPException(422, f"Invalid status. Use: {VALID_STATUSES}")
    with get_db() as db:
        # verify order exists
        if not db.execute("SELECT 1 FROM orders WHERE id=?", (body.order_id,)).fetchone():
            raise HTTPException(404, f"Order {body.order_id} not found")
        # only one report per order
        if db.execute("SELECT 1 FROM reports WHERE order_id=?", (body.order_id,)).fetchone():
            raise HTTPException(409, "Report already exists — use PUT to update")

        cur = db.execute(
            """INSERT INTO reports
               (order_id, radiologist, technique, findings,
                impression, recommendation, status)
               VALUES (?,?,?,?,?,?,?)""",
            (body.order_id, body.radiologist, body.technique,
             body.findings, body.impression, body.recommendation,
             body.status or "DRAFT"),
        )
        rid = cur.lastrowid
        row = db.execute(_FULL_SQL + "WHERE r.id=?", (rid,)).fetchone()
    return row_to_dict(row)


@router.put("/{report_id}")
def update_report(report_id: int, body: ReportUpdate, bg: BackgroundTasks):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Nothing to update")
    if "status" in updates and updates["status"] not in VALID_STATUSES:
        raise HTTPException(422, f"Invalid status. Use: {VALID_STATUSES}")

    set_parts = [f"{k}=?" for k in updates]
    set_parts.append("updated_at=datetime('now')")

    # set finalized_at when transitioning to FINAL
    finalizing = updates.get("status") == "FINAL"
    if finalizing:
        set_parts.append("finalized_at=datetime('now')")

    params = list(updates.values()) + [report_id]

    with get_db() as db:
        existing = db.execute(
            "SELECT id, order_id FROM reports WHERE id=?", (report_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Report not found")

        db.execute(
            f"UPDATE reports SET {', '.join(set_parts)} WHERE id=?", params
        )

        # mark order COMPLETED when report is finalised
        if finalizing:
            db.execute(
                """UPDATE orders SET status='COMPLETED',
                   updated_at=datetime('now') WHERE id=?""",
                (existing["order_id"],),
            )
        
        if finalizing:
            bg.add_task(_notify_fhir_report, report_id, existing["order_id"])

        row = db.execute(_FULL_SQL + "WHERE r.id=?", (report_id,)).fetchone()
    return row_to_dict(row)


async def _notify_fhir_report(report_id: int, order_id: int) -> None:
    _bridge = os.environ.get("FHIR_BRIDGE_URL", "")
    if not _bridge:
        return
    try:
        async with _httpx.AsyncClient(timeout=3) as _c:
            await _c.post(
                f"{_bridge}/api/events/report-final",
                json={"report_id": report_id, "order_id": order_id},
            )
    except Exception:
        pass
