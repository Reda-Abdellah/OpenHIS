from fastapi import APIRouter, Depends, Query
from typing import Optional
from openhis_sdk.auth import require_roles
from database import get_db, rows_to_list

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", dependencies=[Depends(require_roles("admin"))])
def get_audit(master_id: Optional[str] = None, action: Optional[str] = None,
              limit: int = Query(default=200, ge=1, le=1000)):
    clauses, params = [], []
    if master_id: clauses.append("master_id=?"); params.append(master_id)
    if action:    clauses.append("action=?");    params.append(action)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT * FROM audit_log {where} ORDER BY createdat DESC LIMIT ?",
            params
        ).fetchall())
