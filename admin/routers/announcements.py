from fastapi import APIRouter, HTTPException, Depends
from database import get_db, rows_to_list, row_to_dict
from security import audit, require_admin

router = APIRouter(prefix="/api/announcements", tags=["announcements"])

_VALID_SEVERITIES = {'info', 'warning', 'critical', 'success'}


@router.get("")
def list_announcements(
    active_only: bool = True,
    session: dict = Depends(require_admin),
):
    where = "WHERE active=1" if active_only else ""
    with get_db() as db:
        return rows_to_list(db.execute(
            f"SELECT * FROM announcements {where} ORDER BY created_at DESC"
        ).fetchall())


@router.post("", status_code=201)
def create_announcement(body: dict, session: dict = Depends(require_admin)):
    title    = (body.get("title") or "").strip()
    text     = (body.get("body")  or "").strip()
    severity = (body.get("severity") or "info").strip()
    if not title:
        raise HTTPException(400, "title required")
    if not text:
        raise HTTPException(400, "body required")
    if severity not in _VALID_SEVERITIES:
        raise HTTPException(422, f"severity must be one of {sorted(_VALID_SEVERITIES)}")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO announcements(title,body,severity,created_by)"
            " VALUES(?,?,?,?)",
            (title, text, severity, session["username"])
        )
        row = row_to_dict(db.execute(
            "SELECT * FROM announcements WHERE id=?", (cur.lastrowid,)
        ).fetchone())
    audit(session["username"], "announcement-created",
          target=str(row["id"]), detail=title[:80])
    return row


@router.patch("/{aid}")
def update_announcement(aid: int, body: dict,
                        session: dict = Depends(require_admin)):
    allowed = {k: v for k, v in body.items()
               if k in ("active", "title", "body", "severity")}
    if not allowed:
        raise HTTPException(400, "No valid fields provided")
    sets   = ", ".join(f"{k}=?" for k in allowed)
    params = list(allowed.values()) + [aid]
    with get_db() as db:
        if not db.execute(
            "SELECT 1 FROM announcements WHERE id=?", (aid,)
        ).fetchone():
            raise HTTPException(404, "Announcement not found")
        db.execute(f"UPDATE announcements SET {sets} WHERE id=?", params)
        row = row_to_dict(db.execute(
            "SELECT * FROM announcements WHERE id=?", (aid,)
        ).fetchone())
    action = "announcement-deactivated" if allowed.get("active") == 0 else "announcement-updated"
    audit(session["username"], action, target=str(aid))
    return row


@router.delete("/{aid}", status_code=204)
def delete_announcement(aid: int, session: dict = Depends(require_admin)):
    with get_db() as db:
        if not db.execute(
            "SELECT 1 FROM announcements WHERE id=?", (aid,)
        ).fetchone():
            raise HTTPException(404, "Announcement not found")
        db.execute("DELETE FROM announcements WHERE id=?", (aid,))
    audit(session["username"], "announcement-deleted", target=str(aid))
