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
CREATE TABLE IF NOT EXISTS beds (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ward       TEXT NOT NULL,
    room       TEXT,
    bed_label  TEXT NOT NULL,
    bed_type   TEXT DEFAULT 'standard',
    status     TEXT DEFAULT 'available',
    notes      TEXT,
    createdat  TEXT DEFAULT (datetime('now')),
    UNIQUE(ward, bed_label)
);
CREATE INDEX IF NOT EXISTS idx_beds_ward   ON beds(ward);
CREATE INDEX IF NOT EXISTS idx_beds_status ON beds(status);
CREATE TABLE IF NOT EXISTS clinical_notes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id       TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    encounter_id     INTEGER REFERENCES encounters(id),
    note_type        TEXT DEFAULT 'progress',
    title            TEXT,
    content          TEXT NOT NULL,
    status           TEXT DEFAULT 'draft',
    author           TEXT,
    amended_from     INTEGER REFERENCES clinical_notes(id),
    amendment_reason TEXT,
    signed_at        TEXT,
    createdat        TEXT DEFAULT (datetime('now')),
    updatedat        TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS note_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL REFERENCES patients(id),
    encounter_id  INTEGER REFERENCES encounters(id),
    note_id       INTEGER REFERENCES clinical_notes(id),
    filename      TEXT NOT NULL,
    original_name TEXT NOT NULL,
    mime_type     TEXT DEFAULT 'application/octet-stream',
    file_size     INTEGER,
    description   TEXT,
    doc_type      TEXT DEFAULT 'attachment',
    uploaded_by   TEXT,
    createdat     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notes_patient   ON clinical_notes(patient_id);
CREATE INDEX IF NOT EXISTS idx_notes_status    ON clinical_notes(status);
CREATE INDEX IF NOT EXISTS idx_notes_encounter ON clinical_notes(encounter_id);
CREATE INDEX IF NOT EXISTS idx_docs_patient    ON note_documents(patient_id);
CREATE INDEX IF NOT EXISTS idx_docs_note       ON note_documents(note_id);
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
        _beds = [
            ("Cardiology", "101", "101-A", "standard"),
            ("Cardiology", "101", "101-B", "standard"),
            ("Cardiology", "102", "102-A", "icu"),
            ("Neurology",  "201", "201-A", "standard"),
            ("Neurology",  "201", "201-B", "standard"),
            ("ICU",        "001", "ICU-1",  "icu"),
            ("ICU",        "001", "ICU-2",  "icu"),
            ("Surgery",    "301", "301-A",  "standard"),
            ("Surgery",    "301", "301-B",  "isolation"),
            ("Pediatrics", "401", "401-A",  "standard"),
        ]
        for _b in _beds:
            db.execute(
                "INSERT OR IGNORE INTO beds(ward,room,bed_label,bed_type) VALUES(?,?,?,?)", _b
            )


def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

def new_id():
    return str(uuid.uuid4())
