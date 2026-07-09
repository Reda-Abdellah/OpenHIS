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
    created_at    TEXT DEFAULT (datetime('now')),
    ack_status    TEXT,
    ack_at        TEXT,
    ref_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_dir    ON messages(direction, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_type   ON messages(msg_type, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_msg_ctrl   ON messages(control_id);
"""

# Ack-tracking columns (outbound messages that require an acknowledgment)
# were added after first ship: CREATE TABLE IF NOT EXISTS won't migrate an
# existing DB file, so init_db() back-fills them with guarded ALTERs.
_MIGRATIONS = (
    ("ack_status", "ALTER TABLE messages ADD COLUMN ack_status TEXT"),
    ("ack_at",     "ALTER TABLE messages ADD COLUMN ack_at TEXT"),
    ("ref_id",     "ALTER TABLE messages ADD COLUMN ref_id TEXT"),
)


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
        existing = {row[1] for row in db.execute("PRAGMA table_info(messages)").fetchall()}
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                db.execute(ddl)
        # Created here (not in SCHEMA): on a pre-migration DB the column only
        # exists after the ALTERs above have run.
        db.execute("CREATE INDEX IF NOT EXISTS idx_msg_ref ON messages(ref_id)")


def row_to_dict(row):
    return dict(row) if row else None


def rows_to_list(rows):
    return [dict(r) for r in rows]
