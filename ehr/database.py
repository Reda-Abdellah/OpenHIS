import sqlite3, os, uuid
from contextlib import contextmanager

DBPATH = os.environ.get("DBPATH", "data/ehr.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY, mrn TEXT UNIQUE NOT NULL,
    first_name TEXT NOT NULL, last_name TEXT NOT NULL,
    birth_date TEXT, sex TEXT, phone TEXT, insurance_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS encounters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    encounter_type TEXT DEFAULT 'outpatient', admit_date TEXT,
    discharge_date TEXT, ward TEXT, bed TEXT, attending_physician TEXT,
    status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS clinical_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_type TEXT NOT NULL,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    encounter_id INTEGER REFERENCES encounters(id),
    requesting_physician TEXT, order_detail TEXT DEFAULT '{}',
    priority TEXT DEFAULT 'ROUTINE', status TEXT DEFAULT 'PENDING',
    ehr_order_id TEXT, created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cdss_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    alert_type TEXT, severity TEXT DEFAULT 'warning',
    message TEXT, triggered_by TEXT, acknowledged INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS allergies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    substance TEXT NOT NULL, reaction TEXT, severity TEXT DEFAULT 'mild',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    encounter_id INTEGER REFERENCES encounters(id),
    icd10_code TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    provider TEXT, department TEXT, scheduled_date TEXT NOT NULL,
    duration_minutes INTEGER DEFAULT 30, notes TEXT,
    status TEXT DEFAULT 'scheduled', created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS billing_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    encounter_id INTEGER REFERENCES encounters(id),
    cpt_code TEXT NOT NULL, description TEXT, amount REAL NOT NULL,
    status TEXT DEFAULT 'pending', created_at TEXT DEFAULT (datetime('now'))
);
"""

@contextmanager
def get_db():
    os.makedirs(os.path.dirname(DBPATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DBPATH, check_same_thread=False)
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

def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)

def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

def new_id():
    return str(uuid.uuid4())
