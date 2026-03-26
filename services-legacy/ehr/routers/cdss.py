from fastapi import APIRouter
from database import get_db, rows_to_list

router = APIRouter(prefix="/api/cdss", tags=["cdss"])

@router.get("/alerts")
def list_alerts(patient_id: str = None, severity: str = None, unacknowledged: bool = False):
    clauses, params = [], []
    if patient_id:     clauses.append("patient_id=?");    params.append(patient_id)
    if severity:       clauses.append("severity=?");       params.append(severity)
    if unacknowledged: clauses.append("acknowledged=0")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT * FROM cdss_alerts {where} ORDER BY created_at DESC", params).fetchall())

@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int):
    with get_db() as db:
        db.execute("UPDATE cdss_alerts SET acknowledged=1 WHERE id=?", (alert_id,))
    return {"acknowledged": True}
