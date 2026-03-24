from fastapi import APIRouter, Depends
from typing import Optional
from database import get_db, rows_to_list
from security import require_admin

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_audit(
    admin_user: Optional[str] = None,
    action:     Optional[str] = None,
    limit:      int = 200,
    session: dict = Depends(require_admin),
):
    clauses, params = [], []
    if admin_user: clauses.append("admin_user=?"); params.append(admin_user)
    if action:     clauses.append("action=?");     params.append(action)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT * FROM audit_log {where}"
            f" ORDER BY created_at DESC LIMIT ?",
            params
        ).fetchall())
