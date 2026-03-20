import sqlite3, os
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/data/ris.db")

SCHEMA = '''
CREATE TABLE IF NOT EXISTS patients (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    orthanc_id   TEXT    UNIQUE,
    patient_id   TEXT,
    patient_name TEXT    NOT NULL,
    birth_date   TEXT,
    sex          TEXT,
    created_at   TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number     TEXT    UNIQUE,
    patient_id           INTEGER NOT NULL REFERENCES patients(id),
    orthanc_study_id     TEXT,
    modality             TEXT    NOT NULL,
    body_part            TEXT,
    priority             TEXT    DEFAULT 'ROUTINE',
    status               TEXT    DEFAULT 'PENDING',
    requesting_physician TEXT,
    clinical_info        TEXT,
    scheduled_date       TEXT,
    created_at           TEXT    DEFAULT (datetime('now')),
    updated_at           TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       INTEGER UNIQUE REFERENCES orders(id),
    radiologist    TEXT,
    status         TEXT    DEFAULT 'DRAFT',
    technique      TEXT,
    findings       TEXT,
    impression     TEXT,
    recommendation TEXT,
    created_at     TEXT    DEFAULT (datetime('now')),
    updated_at     TEXT    DEFAULT (datetime('now')),
    finalized_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_patient ON orders(patient_id);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders(status);
CREATE INDEX IF NOT EXISTS idx_reports_order  ON reports(order_id);
'''

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA)

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]
