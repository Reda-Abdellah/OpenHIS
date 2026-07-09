import datetime, uuid
from fastapi import Header, HTTPException
from database import get_db, row_to_dict

SESSION_TTL_HOURS = int(__import__('os').environ.get('SESSION_TTL_HOURS', '24'))


def create_session(patient_id: str, patient_mrn: str, patient_name: str) -> str:
    token     = str(uuid.uuid4())
    now       = datetime.datetime.now(datetime.timezone.utc)
    expires   = (now + datetime.timedelta(hours=SESSION_TTL_HOURS)
                 ).isoformat(timespec='seconds')
    with get_db() as db:
        db.execute(
            "INSERT INTO sessions(id,patient_id,patient_mrn,patient_name,expires_at)"
            " VALUES(?,?,?,?,?)",
            (token, patient_id, patient_mrn, patient_name, expires)
        )
    return token


def validate_session(token: str) -> dict | None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM sessions WHERE id=? AND expires_at>?",
            (token, now)
        ).fetchone()
        if row:
            db.execute("UPDATE sessions SET last_seen=? WHERE id=?", (now, token))
            return dict(row)
    return None


def delete_session(token: str):
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE id=?", (token,))


def purge_expired():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE expires_at<?", (now,))


async def require_auth(authorization: str = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    token   = authorization[7:].strip()
    session = validate_session(token)
    if not session:
        raise HTTPException(401, "Session expired or invalid",
                            headers={"WWW-Authenticate": "Bearer"})
    return session
