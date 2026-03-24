from fastapi import APIRouter, HTTPException, Header, Depends
from database  import get_db, row_to_dict
from security  import (hash_password, verify_password,
                       create_admin_session, delete_admin_session,
                       validate_admin_session, audit, require_admin)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: dict):
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    if not username or not password:
        raise HTTPException(400, "username and password required")
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM admin_users WHERE username=?", (username,)
        ).fetchone()
    if not row or not verify_password(password, row["password"]):
        raise HTTPException(401, "Invalid credentials")
    token = create_admin_session(row["id"], username)
    audit(username, "login", detail=f"Successful admin login")
    return {"token": token, "username": username, "role": row["role"]}


@router.post("/logout")
def logout(body: dict = None, session: dict = Depends(require_admin)):
    delete_admin_session(
        ((body or {}).get("token") or "").strip() or
        session.get("id", "")
    )
    audit(session["username"], "logout")
    return {"status": "ok"}


@router.get("/validate")
def validate_token(session: dict = Depends(require_admin)):
    return {
        "valid":    True,
        "username": session["username"],
    }
