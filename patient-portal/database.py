import sqlite3, os
from contextlib import contextmanager

DBPATH = os.environ.get('DB_PATH', 'data/portal.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    patient_id   TEXT NOT NULL,
    patient_mrn  TEXT NOT NULL,
    patient_name TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL,
    last_seen    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS appointment_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id     TEXT NOT NULL,
    patient_mrn    TEXT NOT NULL,
    department     TEXT NOT NULL,
    preferred_date TEXT,
    reason         TEXT,
    status         TEXT DEFAULT 'pending',
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sess_pid    ON sessions(patient_id);
CREATE INDEX IF NOT EXISTS idx_sess_exp    ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_appreq_pid  ON appointment_requests(patient_id);
"""


@contextmanager
def get_db():
    os.makedirs(os.path.dirname(DBPATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DBPATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)


def row_to_dict(row):  return dict(row) if row else None
def rows_to_list(rows): return [dict(r) for r in rows]
