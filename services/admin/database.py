import sqlite3, os
from contextlib import contextmanager

DBPATH = os.environ.get('DB_PATH', 'data/admin.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_user  TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    detail      TEXT,
    ip          TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS system_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT,
    updated_at  TEXT DEFAULT (datetime('now')),
    updated_by  TEXT
);
CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    severity    TEXT DEFAULT 'info',
    active      INTEGER DEFAULT 1,
    created_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_user   ON audit_log(admin_user, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- Service Registry: tracks active services and their health state.
-- Populated by OPM CLI on enable/disable; self-seeded for base services at startup.
CREATE TABLE IF NOT EXISTS service_registry (
    name          TEXT PRIMARY KEY,
    profile       TEXT    NOT NULL DEFAULT 'base',
    internal_url  TEXT    NOT NULL,
    health_url    TEXT    NOT NULL,
    nginx_path    TEXT,
    status        TEXT    DEFAULT 'unknown',
    registered_at TEXT    DEFAULT (datetime('now')),
    last_seen     TEXT,
    metadata      TEXT    DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_reg_profile ON service_registry(profile);
CREATE INDEX IF NOT EXISTS idx_reg_status  ON service_registry(status);
"""

CONFIG_DEFAULTS = [
    ('maintenance_mode',        'false',  'Put system in maintenance mode (true/false)'),
    ('patient_portal_enabled',  'true',   'Enable patient self-service portal'),
    ('hl7_mllp_enabled',        'true',   'Enable HL7 MLLP TCP listener on port 2575'),
    ('ai_auto_trigger_enabled', 'true',   'Enable automatic AI pipeline triggers on DICOM ingest'),
    ('max_login_attempts',      '5',      'Max failed patient-portal login attempts'),
    ('session_timeout_hours',   '24',     'Patient portal session lifetime in hours'),
    ('radiology_sla_hours',     '24',     'Target radiology report turnaround (hours)'),
    ('critical_alert_email',    '',       'Email address for critical lab value alerts'),
]


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


def row_to_dict(row):    return dict(row) if row else None
def rows_to_list(rows):  return [dict(r) for r in rows]


def audit(admin_user: str, action: str,
          target: str = None, detail: str = None, ip: str = None):
    with get_db() as db:
        db.execute(
            "INSERT INTO audit_log(admin_user,action,target,detail,ip)"
            " VALUES(?,?,?,?,?)",
            (admin_user, action, target, detail, ip)
        )


def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)
        # Seed default config keys (ignore if exists)
        for key, val, desc in CONFIG_DEFAULTS:
            db.execute(
                "INSERT OR IGNORE INTO system_config(key,value,description)"
                " VALUES(?,?,?)",
                (key, val, desc)
            )
