import datetime, os
import httpx
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from database import get_db, rows_to_list, row_to_dict

router = APIRouter(prefix="/api/results", tags=["results"])

FHIR_BRIDGE_URL = os.environ.get("FHIR_BRIDGE_URL", "")

class ResultItem(BaseModel):
    analyte: str
    value: str
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    flag: Optional[str] = None   # H / L / HH / LL / normal

class ResultsSubmit(BaseModel):
    order_id: int
    results: List[ResultItem]
    validated_by: Optional[str] = None
    status: Optional[str] = "preliminary"  # preliminary / final

def _compute_flag(value: str, ref_range: Optional[str]) -> Optional[str]:
    """Simple numeric flag computation if reference range is 'low-high'."""
    if not ref_range or not value:
        return None
    try:
        v = float(value)
        parts = ref_range.split("-")
        if len(parts) == 2:
            lo, hi = float(parts[0]), float(parts[1])
            if v < lo:   return "L"
            if v > hi:   return "H"
            return "normal"
    except (ValueError, TypeError):
        pass
    return None

@router.get("/order/{order_id}")
def get_results(order_id: int):
    with get_db() as db:
        return rows_to_list(db.execute(
            "SELECT * FROM lab_results WHERE order_id=? ORDER BY created_at", (order_id,)).fetchall())

@router.post("", status_code=201)
async def submit_results(body: ResultsSubmit, bg: BackgroundTasks):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        if not db.execute("SELECT 1 FROM lab_orders WHERE id=?", (body.order_id,)).fetchone():
            raise HTTPException(404, "Lab order not found")
        ids = []
        for r in body.results:
            flag = r.flag or _compute_flag(r.value, r.reference_range)
            validated_at = now if body.status == "final" else None
            cur = db.execute(
                "INSERT INTO lab_results(order_id,analyte,value,unit,reference_range,flag,status,validated_by,validated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (body.order_id, r.analyte, r.value, r.unit, r.reference_range,
                 flag, body.status, body.validated_by, validated_at))
            ids.append(cur.lastrowid)
        if body.status == "final":
            db.execute("UPDATE lab_orders SET status='COMPLETED', updated_at=? WHERE id=?",
                       (now, body.order_id))
        # Collect for FHIR bridge notification
        order_row = db.execute(
            "SELECT lo.*, lp.ehr_patient_id, lp.patient_name, s.accession_number "
            "FROM lab_orders lo JOIN specimens s ON s.id=lo.specimen_id "
            "JOIN lab_patients lp ON lp.id=s.patient_id WHERE lo.id=?",
            (body.order_id,)).fetchone()
        result_rows = rows_to_list(db.execute(
            "SELECT * FROM lab_results WHERE order_id=?", (body.order_id,)).fetchall())

    if body.status == "final" and FHIR_BRIDGE_URL:
        payload = {
            "order_id": body.order_id,
            "ehr_patient_id": dict(order_row).get("ehr_patient_id"),
            "patient_name": dict(order_row).get("patient_name"),
            "accession_number": dict(order_row).get("accession_number"),
            "test_code": dict(order_row).get("test_code"),
            "results": result_rows,
        }
        bg.add_task(_notify_bridge, payload)

    return {"created": len(ids), "order_id": body.order_id, "status": body.status}

async def _notify_bridge(payload: dict):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(f"{FHIR_BRIDGE_URL}/api/events/lab-result-final", json=payload)
    except Exception:
        pass

@router.patch("/{result_id}/validate")
def validate_result(result_id: int, body: dict):
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "UPDATE lab_results SET status='final', validated_by=?, validated_at=? WHERE id=?",
            (body.get("validated_by"), now, result_id))
        row = db.execute("SELECT * FROM lab_results WHERE id=?", (result_id,)).fetchone()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, "Result not found")
    return row_to_dict(row)
