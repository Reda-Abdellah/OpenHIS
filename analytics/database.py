import sqlite3, os, uuid
from contextlib import contextmanager

DBPATH = os.environ.get('DB_PATH', 'data/analytics.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT NOT NULL,
    data        TEXT NOT NULL,
    captured_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_snap_domain ON snapshots(domain, captured_at);
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
