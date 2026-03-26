"""
SQLite-backed audit log for integration-hub.

Every sync event (success, failure, retry) is appended to the audit_events
table. The DB file path is set via config.AUDIT_DB_PATH (default: /data/hub-audit.db).
"""
import os
import aiosqlite
from datetime import datetime, timezone

from app.config import AUDIT_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    event_type    TEXT    NOT NULL,
    resource_type TEXT    DEFAULT '',
    resource_id   TEXT    DEFAULT '',
    direction     TEXT    DEFAULT '',
    status        TEXT    NOT NULL,
    detail        TEXT    DEFAULT '',
    attempts      INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_audit_ts    ON audit_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_rtype ON audit_events(resource_type);
CREATE INDEX IF NOT EXISTS idx_audit_etype ON audit_events(event_type);
"""


async def init_audit_db() -> None:
    os.makedirs(os.path.dirname(AUDIT_DB_PATH), exist_ok=True)
    async with aiosqlite.connect(AUDIT_DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def log_event(
    event_type: str,
    resource_type: str = "",
    resource_id: str = "",
    direction: str = "",
    status: str = "ok",
    detail: str = "",
    attempts: int = 1,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    try:
        async with aiosqlite.connect(AUDIT_DB_PATH) as db:
            await db.execute(
                "INSERT INTO audit_events"
                " (ts, event_type, resource_type, resource_id,"
                "  direction, status, detail, attempts)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (ts, event_type, resource_type, resource_id,
                 direction, status, detail, attempts),
            )
            await db.commit()
    except Exception:
        pass  # Audit failure must never break the sync path


async def query_events(
    limit: int = 100,
    offset: int = 0,
    event_type: str = "",
    resource_type: str = "",
) -> list[dict]:
    filters: list[str] = []
    params: list = []
    if event_type:
        filters.append("event_type=?")
        params.append(event_type)
    if resource_type:
        filters.append("resource_type=?")
        params.append(resource_type)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params += [limit, offset]

    async with aiosqlite.connect(AUDIT_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM audit_events {where} ORDER BY ts DESC LIMIT ? OFFSET ?",
            params,
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
