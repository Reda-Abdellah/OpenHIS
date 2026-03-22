import datetime, json, os
import httpx
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from database import get_db, rows_to_list, row_to_dict
from cdss_engine import evaluate_order

router = APIRouter(prefix="/api/orders", tags=["orders"])

FHIR_BRIDGE_URL = os.environ.get("FHIR_BRIDGE_URL", "")

ORDER_SQL = """
    SELECT o.*, p.first_name || ' ' || p.last_name AS patient_name, p.mrn
    FROM clinical_orders o
    JOIN patients p ON p.id = o.patient_id
"""

class OrderCreate(BaseModel):
    order_type: str                         # LAB / IMAGING / PHARMACY
    patient_id: str
    encounter_id: Optional[int] = None
    requesting_physician: Optional[str] = None
    order_detail: Optional[dict] = {}
    priority: Optional[str] = "ROUTINE"

class OrderUpdate(BaseModel):
    status: Optional[str] = None
    external_ref: Optional[str] = None
    priority: Optional[str] = None

@router.get("")
def list_orders(order_type: Optional[str] = None, status: Optional[str] = None,
                patient_id: Optional[str] = None):
    clauses, params = [], []
    if order_type: clauses.append("o.order_type=?");  params.append(order_type.upper())
    if status:     clauses.append("o.status=?");       params.append(status)
    if patient_id: clauses.append("o.patient_id=?");   params.append(patient_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(f"{ORDER_SQL} {where} ORDER BY o.created_at DESC", params).fetchall())

@router.get("/{order_id}")
def get_order(order_id: int):
    with get_db() as db:
        row = db.execute(f"{ORDER_SQL} WHERE o.id=?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        return dict(row)

@router.post("", status_code=201)
async def create_order(body: OrderCreate, bg: BackgroundTasks):
    order_type = body.order_type.upper()
    if order_type not in ("LAB", "IMAGING", "PHARMACY", "REFERRAL"):
        raise HTTPException(422, "order_type must be LAB, IMAGING, PHARMACY, or REFERRAL")
    with get_db() as db:
        if not db.execute("SELECT 1 FROM patients WHERE id=?", (body.patient_id,)).fetchone():
            raise HTTPException(404, "Patient not found")
        cur = db.execute(
            "INSERT INTO clinical_orders(order_type,patient_id,encounter_id,requesting_physician,order_detail,priority) VALUES(?,?,?,?,?,?)",
            (order_type, body.patient_id, body.encounter_id, body.requesting_physician,
             json.dumps(body.order_detail or {}), body.priority or "ROUTINE"))
        oid = cur.lastrowid
        row = db.execute(f"{ORDER_SQL} WHERE o.id=?", (oid,)).fetchone()
        order = dict(row)

    # CDSS check on new order
    alerts = evaluate_order(order)
    if alerts:
        with get_db() as db:
            for a in alerts:
                db.execute(
                    "INSERT INTO cdss_alerts(patient_id,alert_type,severity,message,triggered_by) VALUES(?,?,?,?,?)",
                    (body.patient_id, a["type"], a["severity"], a["message"], f"order:{oid}"))

    # Notify FHIR bridge for routing (LAB → LIS, IMAGING → RIS)
    if FHIR_BRIDGE_URL:
        event = "imaging-order" if order_type == "IMAGING" else "lab-order" if order_type == "LAB" else None
        if event:
            bg.add_task(_notify_bridge, event, order)

    return order

async def _notify_bridge(event: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(f"{FHIR_BRIDGE_URL}/events/{event}", json=payload)
    except Exception:
        pass

@router.patch("/{order_id}")
def update_order(order_id: int, body: OrderUpdate):
    allowed = {"status", "external_ref", "priority"}
    updates = {k: v for k, v in body.model_dump().items() if v is not None and k in allowed}
    if not updates:
        raise HTTPException(400, "No fields to update")
    updates["updated_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_db() as db:
        db.execute(f"UPDATE clinical_orders SET {sets} WHERE id=?", (*updates.values(), order_id))
        return row_to_dict(db.execute(f"{ORDER_SQL} WHERE o.id=?", (order_id,)).fetchone())

@router.post("/from-lis-result")
async def receive_lab_result(payload: dict):
    """Called by FHIR bridge when LIS finalises results. Triggers CDSS."""
    from cdss_engine import evaluate_lab_result
    patient_id = payload.get("ehr_patient_id")
    if not patient_id:
        return {"status": "skipped", "reason": "no ehr_patient_id"}

    alerts = evaluate_lab_result(payload)
    if alerts:
        with get_db() as db:
            for a in alerts:
                db.execute(
                    "INSERT INTO cdss_alerts(patient_id,alert_type,severity,message,triggered_by) VALUES(?,?,?,?,?)",
                    (patient_id, a["type"], a["severity"], a["message"], f"lis:{payload.get('order_id')}"))
    return {"status": "ok", "alerts_created": len(alerts)}
