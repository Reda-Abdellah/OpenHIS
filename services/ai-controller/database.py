import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/data/ai-controller.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipelines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    docker_image TEXT NOT NULL,
    version     TEXT DEFAULT '1.0.0',
    source_type TEXT NOT NULL DEFAULT 'imaging',
    enabled     INTEGER DEFAULT 1,
    output_types TEXT DEFAULT '["report"]',
    config_json TEXT DEFAULT '{}',
    input_schema TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    modality    TEXT,
    body_part   TEXT,
    trigger_filter TEXT DEFAULT '{}',
    auto_trigger  INTEGER DEFAULT 0,
    auto_saveback INTEGER DEFAULT 0,
    saveback_types TEXT DEFAULT '["report"]',
    priority    INTEGER DEFAULT 0,
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    pipeline_id      TEXT NOT NULL REFERENCES pipelines(id),
    rule_id          INTEGER REFERENCES rules(id),
    series_uid       TEXT,
    study_uid        TEXT,
    patient_name     TEXT,
    patient_id       TEXT,
    modality         TEXT,
    body_part        TEXT,
    accession_number TEXT,
    orthanc_series_id TEXT,
    orthanc_study_id  TEXT,
    source_type      TEXT DEFAULT 'imaging',
    event_source_id  TEXT,
    event_payload    TEXT DEFAULT '{}',
    status           TEXT DEFAULT 'PENDING',
    trigger_type     TEXT DEFAULT 'MANUAL',
    container_id     TEXT,
    container_logs   TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    started_at       TEXT,
    finished_at      TEXT,
    duration_ms      INTEGER,
    error            TEXT,
    result_summary   TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id             TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    direction          TEXT NOT NULL,
    artifact_type      TEXT NOT NULL,
    filename           TEXT NOT NULL,
    rel_path           TEXT NOT NULL,
    size_bytes         INTEGER,
    dicom_sop_class    TEXT,
    dicom_instance_uid TEXT,
    orthanc_instance_id TEXT,
    created_at         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS saveback_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT NOT NULL REFERENCES jobs(id),
    artifact_id         INTEGER REFERENCES artifacts(id),
    orthanc_instance_id TEXT,
    status              TEXT DEFAULT 'PENDING',
    trigger_type        TEXT DEFAULT 'MANUAL',
    error               TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    completed_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_series   ON jobs(series_uid);
CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_pipeline ON jobs(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id);
CREATE INDEX IF NOT EXISTS idx_saveback_job  ON saveback_events(job_id);
"""

SEED_PIPELINES = [
    {
        "id": "poc-xray",
        "name": "POC Chest X-Ray",
        "description": "Skeleton X-ray pipeline: reads DICOM series, generates random findings + Secondary Capture overlay.",
        "docker_image": "openhis/poc-xray:latest",
        "version": "1.0.0",
        "source_type": "imaging",
        "output_types": '["report", "secondary_capture"]',
        "config_json": '{"confidence_threshold": 0.6}',
        "input_schema": '{}',
    },
    {
        "id": "poc-ct",
        "name": "POC CT Analyzer",
        "description": "Skeleton CT pipeline: reads DICOM series, generates random segmentation mask + findings report.",
        "docker_image": "openhis/poc-ct:latest",
        "version": "1.0.0",
        "source_type": "imaging",
        "output_types": '["report", "segmentation"]',
        "config_json": '{"body_part_override": null}',
        "input_schema": '{}',
    },
    {
        "id": "poc-lab-risk",
        "name": "POC Lab Risk Scorer",
        "description": "Demo pipeline: reads a lab DiagnosticReport JSON, outputs risk_score + findings.",
        "docker_image": "openhis/poc-lab-risk:latest",
        "version": "1.0.0",
        "source_type": "lab_result",
        "output_types": '["report"]',
        "config_json": '{"risk_threshold": 0.7}',
        "input_schema": '{"oe_id": "string", "subject": "string"}',
    },
    {
        "id": "poc-emr-alert",
        "name": "POC EMR Clinical Alert",
        "description": "Demo pipeline: reads a patient.synced payload, outputs clinical risk flags.",
        "docker_image": "openhis/poc-emr-alert:latest",
        "version": "1.0.0",
        "source_type": "emr_event",
        "output_types": '["report"]',
        "config_json": '{}',
        "input_schema": '{"mrn": "string", "omrs_id": "string"}',
    },
]

SEED_RULES = [
    {
        "pipeline_id": "poc-xray",
        "name": "Auto \u2013 Chest X-Ray (CR/DX)",
        "modality": "CR,DX",
        "body_part": "CHEST,THORAX",
        "trigger_filter": "{}",
        "auto_trigger": 1,
        "auto_saveback": 0,
        "saveback_types": '["report", "secondary_capture"]',
        "priority": 10,
        "enabled": 1,
    },
    {
        "pipeline_id": "poc-ct",
        "name": "Auto \u2013 Chest CT",
        "modality": "CT",
        "body_part": "CHEST",
        "trigger_filter": "{}",
        "auto_trigger": 1,
        "auto_saveback": 0,
        "saveback_types": '["report", "segmentation"]',
        "priority": 10,
        "enabled": 1,
    },
    {
        "pipeline_id": "poc-lab-risk",
        "name": "Auto \u2013 Lab Result (all panels)",
        "modality": None,
        "body_part": None,
        "trigger_filter": "{}",
        "auto_trigger": 0,
        "auto_saveback": 0,
        "saveback_types": '["report"]',
        "priority": 5,
        "enabled": 1,
    },
    {
        "pipeline_id": "poc-emr-alert",
        "name": "Auto \u2013 Patient Synced",
        "modality": None,
        "body_part": None,
        "trigger_filter": "{}",
        "auto_trigger": 0,
        "auto_saveback": 0,
        "saveback_types": '["report"]',
        "priority": 5,
        "enabled": 1,
    },
]


def _migrate(db: sqlite3.Connection) -> None:
    """
    Idempotent migrations for databases created before the multi-source extension.
    Uses ALTER TABLE … ADD COLUMN which is a no-op if the column already exists
    (SQLite raises OperationalError "duplicate column name" which we swallow).
    The jobs table previously had NOT NULL on series_uid / study_uid; we handle
    that by checking for the old schema and recreating the table once.
    """
    def _add_col(table: str, col: str, defn: str):
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        except Exception:
            pass  # column already exists

    # pipelines
    _add_col("pipelines", "source_type", "TEXT NOT NULL DEFAULT 'imaging'")
    _add_col("pipelines", "input_schema", "TEXT DEFAULT '{}'")

    # rules
    _add_col("rules", "trigger_filter", "TEXT DEFAULT '{}'")

    # jobs — add new columns first, then handle nullable series_uid
    _add_col("jobs", "source_type",      "TEXT DEFAULT 'imaging'")
    _add_col("jobs", "event_source_id",  "TEXT")
    _add_col("jobs", "event_payload",    "TEXT DEFAULT '{}'")
    # event_source index must be created after the column exists
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_event_source ON jobs(event_source_id)")
    except Exception:
        pass

    # Check if series_uid is still NOT NULL by looking at the CREATE TABLE SQL
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row and "series_uid       TEXT NOT NULL" in row[0]:
        # Recreate jobs table without NOT NULL constraints on series_uid/study_uid
        db.executescript("""
            ALTER TABLE jobs RENAME TO jobs_v1;
            CREATE TABLE jobs (
                id               TEXT PRIMARY KEY,
                pipeline_id      TEXT NOT NULL REFERENCES pipelines(id),
                rule_id          INTEGER REFERENCES rules(id),
                series_uid       TEXT,
                study_uid        TEXT,
                patient_name     TEXT,
                patient_id       TEXT,
                modality         TEXT,
                body_part        TEXT,
                accession_number TEXT,
                orthanc_series_id TEXT,
                orthanc_study_id  TEXT,
                source_type      TEXT DEFAULT 'imaging',
                event_source_id  TEXT,
                event_payload    TEXT DEFAULT '{}',
                status           TEXT DEFAULT 'PENDING',
                trigger_type     TEXT DEFAULT 'MANUAL',
                container_id     TEXT,
                container_logs   TEXT,
                created_at       TEXT DEFAULT (datetime('now')),
                started_at       TEXT,
                finished_at      TEXT,
                duration_ms      INTEGER,
                error            TEXT,
                result_summary   TEXT
            );
            INSERT INTO jobs
                (id, pipeline_id, rule_id, series_uid, study_uid,
                 patient_name, patient_id, modality, body_part, accession_number,
                 orthanc_series_id, orthanc_study_id,
                 status, trigger_type, container_id, container_logs,
                 created_at, started_at, finished_at, duration_ms, error, result_summary)
            SELECT
                id, pipeline_id, rule_id, series_uid, study_uid,
                patient_name, patient_id, modality, body_part, accession_number,
                orthanc_series_id, orthanc_study_id,
                status, trigger_type, container_id, container_logs,
                created_at, started_at, finished_at, duration_ms, error, result_summary
            FROM jobs_v1;
            DROP TABLE jobs_v1;
            CREATE INDEX IF NOT EXISTS idx_jobs_series       ON jobs(series_uid);
            CREATE INDEX IF NOT EXISTS idx_jobs_status       ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_pipeline     ON jobs(pipeline_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_event_source ON jobs(event_source_id);
        """)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA)
        _migrate(db)
        for p in SEED_PIPELINES:
            db.execute(
                "INSERT OR IGNORE INTO pipelines"
                " (id,name,description,docker_image,version,source_type,output_types,config_json,input_schema)"
                " VALUES (:id,:name,:description,:docker_image,:version,:source_type,:output_types,:config_json,:input_schema)",
                p,
            )
        for r in SEED_RULES:
            exists = db.execute(
                "SELECT 1 FROM rules WHERE pipeline_id=? AND name=?",
                (r["pipeline_id"], r["name"]),
            ).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO rules"
                    " (pipeline_id,name,modality,body_part,trigger_filter,"
                    "  auto_trigger,auto_saveback,saveback_types,priority,enabled)"
                    " VALUES (:pipeline_id,:name,:modality,:body_part,:trigger_filter,"
                    "         :auto_trigger,:auto_saveback,:saveback_types,:priority,:enabled)",
                    r,
                )


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
