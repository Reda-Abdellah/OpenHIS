import datetime, hashlib, binascii, os, uuid
from fastapi import Header, HTTPException
from database import get_db

SESSION_TTL_HOURS = int(os.environ.get('SESSION_TTL_HOURS', '12'))
_ITERS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(32)
    key  = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, _ITERS)
    return binascii.hexlify(salt).decode() + ':' + binascii.hexlify(key).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(':', 1)
        salt = binascii.unhexlify(salt_hex)
        key  = binascii.unhexlify(key_hex)
        return hashlib.pbkdf2_hmac(
            'sha256', password.encode(), salt, _ITERS
        ) == key
    except Exception:
        return False


def create_admin_session(user_id: int, username: str) -> str:
    token   = str(uuid.uuid4())
    now     = datetime.datetime.utcnow()
    expires = (now + datetime.timedelta(hours=SESSION_TTL_HOURS)
               ).isoformat(timespec='seconds')
    with get_db() as db:
        db.execute(
            "INSERT INTO admin_sessions(id,user_id,username,expires_at)"
            " VALUES(?,?,?,?)",
            (token, user_id, username, expires)
        )
        db.execute(
            "UPDATE admin_users SET last_login=? WHERE id=?",
            (now.isoformat(timespec='seconds'), user_id)
        )
    return token


def validate_admin_session(token: str) -> dict | None:
    now = datetime.datetime.utcnow().isoformat(timespec='seconds')
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM admin_sessions WHERE id=? AND expires_at>?",
            (token, now)
        ).fetchone()
    return dict(row) if row else None


def delete_admin_session(token: str):
    with get_db() as db:
        db.execute("DELETE FROM admin_sessions WHERE id=?", (token,))


def purge_expired_sessions():
    now = datetime.datetime.utcnow().isoformat(timespec='seconds')
    with get_db() as db:
        db.execute("DELETE FROM admin_sessions WHERE expires_at<?", (now,))


def audit(admin_user: str, action: str,
          target: str = None, detail: str = None, ip: str = None):
    with get_db() as db:
        db.execute(
            "INSERT INTO audit_log(admin_user,action,target,detail,ip)"
            " VALUES(?,?,?,?,?)",
            (admin_user, action, target, detail, ip)
        )


async def require_admin(authorization: str = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, "Admin authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    token   = authorization[7:].strip()
    session = validate_admin_session(token)
    if not session:
        raise HTTPException(401, "Session expired or invalid",
                            headers={"WWW-Authenticate": "Bearer"})
    return session
