import datetime
from fastapi import APIRouter, HTTPException, Depends
from database import get_db, rows_to_list, row_to_dict, audit
from jwt_auth import require_token

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def list_config(claims: dict = Depends(require_token)):
    with get_db() as db:
        return rows_to_list(db.execute(
            "SELECT * FROM system_config ORDER BY key"
        ).fetchall())


@router.get("/{key}")
def get_config(key: str, claims: dict = Depends(require_token)):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM system_config WHERE key=?", (key,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Config key '{key}' not found")
    return row_to_dict(row)


@router.put("/{key}")
def set_config(key: str, body: dict, claims: dict = Depends(require_token)):
    value = body.get("value")
    if value is None:
        raise HTTPException(400, "'value' required")
    value = str(value).strip()
    now   = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    username = claims.get("preferred_username", "unknown")
    with get_db() as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at,updated_by)"
            " VALUES(?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET"
            " value=excluded.value, updated_at=excluded.updated_at,"
            " updated_by=excluded.updated_by",
            (key, value, now, username)
        )
        row = row_to_dict(db.execute(
            "SELECT * FROM system_config WHERE key=?", (key,)
        ).fetchone())
    audit(username, "config-changed", target=key, detail=f"value={value[:80]}")
    return row
