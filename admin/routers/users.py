from fastapi import APIRouter, HTTPException, Depends
from database import get_db, rows_to_list, row_to_dict
from security import hash_password, audit, require_admin

router = APIRouter(prefix="/api/users", tags=["users"])

_SAFE = {"id", "username", "role", "created_at", "last_login"}


def _safe(row: dict) -> dict:
    return {k: v for k, v in row.items() if k in _SAFE}


@router.get("")
def list_users(session: dict = Depends(require_admin)):
    with get_db() as db:
        rows = rows_to_list(db.execute(
            "SELECT * FROM admin_users ORDER BY created_at"
        ).fetchall())
    return [_safe(r) for r in rows]


@router.post("", status_code=201)
def create_user(body: dict, session: dict = Depends(require_admin)):
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    role     = (body.get("role") or "admin").strip()
    if not username or not password:
        raise HTTPException(400, "username and password required")
    with get_db() as db:
        if db.execute(
            "SELECT 1 FROM admin_users WHERE username=?", (username,)
        ).fetchone():
            raise HTTPException(409, f"Username '{username}' already exists")
        cur = db.execute(
            "INSERT INTO admin_users(username,password,role) VALUES(?,?,?)",
            (username, hash_password(password), role)
        )
        row = row_to_dict(db.execute(
            "SELECT * FROM admin_users WHERE id=?", (cur.lastrowid,)
        ).fetchone())
    audit(session["username"], "user-created",
          target=username, detail=f"role={role}")
    return _safe(row)


@router.patch("/{uid}/password", status_code=200)
def change_password(uid: int, body: dict,
                    session: dict = Depends(require_admin)):
    new_pw = (body.get("password") or "").strip()
    if len(new_pw) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    with get_db() as db:
        if not db.execute(
            "SELECT 1 FROM admin_users WHERE id=?", (uid,)
        ).fetchone():
            raise HTTPException(404, "User not found")
        db.execute(
            "UPDATE admin_users SET password=? WHERE id=?",
            (hash_password(new_pw), uid)
        )
    audit(session["username"], "password-changed", target=str(uid))
    return {"status": "ok"}


@router.delete("/{uid}", status_code=204)
def delete_user(uid: int, session: dict = Depends(require_admin)):
    with get_db() as db:
        row = db.execute(
            "SELECT username FROM admin_users WHERE id=?", (uid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        if row["username"] == session["username"]:
            raise HTTPException(403, "Cannot delete your own account")
        db.execute("DELETE FROM admin_users WHERE id=?", (uid,))
    audit(session["username"], "user-deleted", target=row["username"])
