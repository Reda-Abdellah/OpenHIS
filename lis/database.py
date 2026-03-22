import sqlite3, os, random, datetime
from contextlib import contextmanager

DBPATH = os.environ.get("DBPATH", "data/lis.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lab_patients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ehr_patient_id  TEXT,
    patient_name    TEXT,
    patient_dob     TEXT,
    mrn             TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS specimens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    accession       TEXT UNIQUE NOT NULL,
    patient_id      INTEGER REFERENCES lab_patients(id),
    specimen_type   TEXT,
    collection_date TEXT,
    collected_by    TEXT,
    received_date   TEXT,
    received_by     TEXT,
    status          TEXT DEFAULT 'collected',
    custody_log     TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lab_orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ehr_order_id   TEXT,
    specimen_id    INTEGER REFERENCES specimens(id),
    test_code      TEXT NOT NULL,
    test_name      TEXT,
    priority       TEXT DEFAULT 'ROUTINE',
    status         TEXT DEFAULT 'PENDING',
    instrument_id  TEXT,
    ordered_by     TEXT,
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lab_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES lab_orders(id),
    analyte         TEXT NOT NULL,
    value           TEXT,
    unit            TEXT,
    reference_range TEXT,
    flag            TEXT,
    status          TEXT DEFAULT 'preliminary',
    validated_by    TEXT,
    validated_at    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS qc_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id  TEXT,
    test_code      TEXT,
    lot_number     TEXT,
    qc_level       TEXT,
    result_value   REAL,
    expected_mean  REAL,
    expected_sd    REAL,
    westgard_flag  TEXT,
    pass           INTEGER,
    recorded_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS instrument_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id      TEXT,
    instrument_type    TEXT,
    run_started        TEXT,
    run_finished       TEXT,
    orders_processed   INTEGER DEFAULT 0,
    status             TEXT DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS idx_specimens_patient ON specimens(patient_id);
CREATE INDEX IF NOT EXISTS idx_orders_specimen   ON lab_orders(specimen_id);
CREATE INDEX IF NOT EXISTS idx_results_order     ON lab_results(order_id);
CREATE INDEX IF NOT EXISTS idx_qc_instrument     ON qc_records(instrument_id);
"""

def gen_accession():
    d = datetime.date.today().strftime("%Y%m%d")
    n = random.randint(1000, 9999)
    return f"LAB-{d}-{n}"

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
