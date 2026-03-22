import sqlite3, os, uuid
from contextlib import contextmanager

DBPATH = os.environ.get("DBPATH", "data/ehr.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY, mrn TEXT UNIQUE NOT NULL,
    firstname TEXT NOT NULL, lastname TEXT NOT NULL,
    birthdate TEXT, sex TEXT, phone TEXT, insurance_id TEXT,
    createdat TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS encounters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patientid TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    encountertype TEXT DEFAULT 'outpatient', admitdate TEXT,
    dischargedate TEXT, ward TEXT, bed TEXT, attendingphysician TEXT,
    status TEXT DEFAULT 'active', createdat TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS clinicalorders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ordertype TEXT NOT NULL,
    patientid TEXT NOT NULL REFERENCES patients(id),
    encounterid INTEGER REFERENCES encounters(id),
    requestingphysician TEXT, orderdetail TEXT DEFAULT '{}',
    priority TEXT DEFAULT 'ROUTINE', status TEXT DEFAULT 'PENDING',
    externalref TEXT, createdat TEXT DEFAULT (datetime('now')),
    updatedat TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cdssalerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patientid TEXT NOT NULL REFERENCES patients(id),
    alerttype TEXT, severity TEXT DEFAULT 'warning',
    message TEXT, triggeredby TEXT, acknowledged INTEGER DEFAULT 0,
    createdat TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS allergies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patientid TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    substance TEXT NOT NULL, reaction TEXT, severity TEXT DEFAULT 'mild',
    createdat TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patientid TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    encounterid INTEGER REFERENCES encounters(id),
    icd10code TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'active',
    createdat TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patientid TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    provider TEXT, department TEXT, scheduleddate TEXT NOT NULL,
    durationminutes INTEGER DEFAULT 30, notes TEXT,
    status TEXT DEFAULT 'scheduled', createdat TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS billingrecords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patientid TEXT NOT NULL REFERENCES patients(id),
    encounterid INTEGER REFERENCES encounters(id),
    cptcode TEXT NOT NULL, description TEXT, amount REAL NOT NULL,
    status TEXT DEFAULT 'pending', createdat TEXT DEFAULT (datetime('now'))
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

# camelCase aliases used by main.py / health check
getdb      = get_db
initdb     = init_db
rowtodict  = row_to_dict
rowstolist = rows_to_list
newid      = new_id
