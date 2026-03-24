import sqlite3, os
from contextlib import contextmanager

DBPATH = os.environ.get('DB_PATH', 'data/hl7.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    direction     TEXT NOT NULL,
    msg_type      TEXT NOT NULL,
    control_id    TEXT,
    sending_app   TEXT,
    patient_id    TEXT,
    patient_name  TEXT,
    raw           TEXT NOT NULL,
    status        TEXT DEFAULT 'received',
    error_msg     TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_msg_dir    ON messages(direction, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_type   ON messages(msg_type, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_msg_ctrl   ON messages(control_id);
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


def row_to_dict(row):
    return dict(row) if row else None


def rows_to_list(rows):
    return [dict(r) for r in rows]
