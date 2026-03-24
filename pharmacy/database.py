import sqlite3, os
from contextlib import contextmanager

DBPATH = os.environ.get('DB_PATH', 'data/pharmacy.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS medications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    generic_name TEXT,
    form         TEXT DEFAULT 'tablet',
    strength     TEXT,
    route        TEXT DEFAULT 'oral',
    unit         TEXT DEFAULT 'mg',
    controlled   INTEGER DEFAULT 0,
    notes        TEXT,
    active       INTEGER DEFAULT 1,
    createdat    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS prescriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ehr_order_id    TEXT,
    ehr_patient_id  TEXT NOT NULL,
    medication_id   INTEGER REFERENCES medications(id),
    drug_name       TEXT NOT NULL,
    dose            TEXT NOT NULL,
    route           TEXT DEFAULT 'oral',
    frequency       TEXT NOT NULL,
    duration_days   INTEGER,
    quantity        INTEGER DEFAULT 1,
    prescriber      TEXT,
    notes           TEXT,
    status          TEXT DEFAULT 'pending',
    verified_by     TEXT,
    verifiedat      TEXT,
    createdat       TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dispenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id INTEGER NOT NULL REFERENCES prescriptions(id),
    ehr_patient_id  TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    dispensed_by    TEXT,
    lot_number      TEXT,
    expiry_date     TEXT,
    dispensedat     TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mar_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id INTEGER NOT NULL REFERENCES prescriptions(id),
    ehr_patient_id  TEXT NOT NULL,
    administered_by TEXT,
    dose_given      TEXT,
    route           TEXT,
    status          TEXT DEFAULT 'given',
    notes           TEXT,
    administeredat  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS stock (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    quantity      INTEGER NOT NULL DEFAULT 0,
    lot_number    TEXT,
    expiry_date   TEXT,
    location      TEXT DEFAULT 'main',
    low_threshold INTEGER DEFAULT 10,
    updatedat     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rx_patient  ON prescriptions(ehr_patient_id);
CREATE INDEX IF NOT EXISTS idx_rx_status   ON prescriptions(status);
CREATE INDEX IF NOT EXISTS idx_rx_order    ON prescriptions(ehr_order_id);
CREATE INDEX IF NOT EXISTS idx_stock_med   ON stock(medication_id);
"""

SEED_MEDS = [
    ("Amoxicillin",    "Amoxicillin",       "capsule",  "500mg", "oral",     "mg",  0),
    ("Ibuprofen",      "Ibuprofen",         "tablet",   "400mg", "oral",     "mg",  0),
    ("Metoprolol",     "Metoprolol",        "tablet",   "50mg",  "oral",     "mg",  0),
    ("Furosemide",     "Furosemide",        "tablet",   "40mg",  "oral",     "mg",  0),
    ("Morphine",       "Morphine Sulfate",  "injection","10mg",  "iv",       "mg",  1),
    ("Metformin",      "Metformin HCl",     "tablet",   "850mg", "oral",     "mg",  0),
    ("Atorvastatin",   "Atorvastatin",      "tablet",   "20mg",  "oral",     "mg",  0),
    ("Pantoprazole",   "Pantoprazole",      "tablet",   "40mg",  "oral",     "mg",  0),
    ("Paracetamol",    "Acetaminophen",     "tablet",   "1000mg","oral",     "mg",  0),
    ("NaCl 0.9%",      "Sodium Chloride",   "liquid",   "500ml", "iv",       "ml",  0),
]


@contextmanager
def get_db():
    os.makedirs(os.path.dirname(DBPATH) or '.', exist_ok=True)
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
        for m in SEED_MEDS:
            db.execute(
                "INSERT OR IGNORE INTO medications"
                "(name,generic_name,form,strength,route,unit,controlled) VALUES(?,?,?,?,?,?,?)",
                m
            )
        for mid in db.execute("SELECT id FROM medications").fetchall():
            existing = db.execute(
                "SELECT 1 FROM stock WHERE medication_id=?", (mid[0],)
            ).fetchone()
            if not existing:
                db.execute(
                    "INSERT INTO stock(medication_id,quantity,location) VALUES(?,?,?)",
                    (mid[0], 100, "main")
                )


def row_to_dict(row): return dict(row) if row else None
def rows_to_list(rows): return [dict(r) for r in rows]
