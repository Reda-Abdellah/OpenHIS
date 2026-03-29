import os
import uuid
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get(
    "MPI_DATABASE_URL",
    "postgresql://mpi:mpi@postgres:5432/mpi",
)

# Schema DDL (PostgreSQL syntax)
_SCHEMA_STMTS = [
    """
    CREATE TABLE IF NOT EXISTS master_patients (
        id           TEXT PRIMARY KEY,
        mrn          TEXT UNIQUE NOT NULL,
        firstname    TEXT NOT NULL,
        lastname     TEXT NOT NULL,
        birthdate    TEXT,
        sex          TEXT,
        phone        TEXT,
        address      TEXT,
        insurance_id TEXT,
        status       TEXT DEFAULT 'active',
        merged_into  TEXT REFERENCES master_patients(id),
        createdat    TEXT DEFAULT (NOW()::TEXT),
        updatedat    TEXT DEFAULT (NOW()::TEXT)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cross_references (
        id          SERIAL PRIMARY KEY,
        master_id   TEXT NOT NULL REFERENCES master_patients(id) ON DELETE CASCADE,
        system      TEXT NOT NULL,
        system_id   TEXT NOT NULL,
        mrn         TEXT,
        assigning_authority TEXT,
        createdat   TEXT DEFAULT (NOW()::TEXT),
        UNIQUE(system, system_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS match_candidates (
        id           SERIAL PRIMARY KEY,
        master_id_a  TEXT NOT NULL REFERENCES master_patients(id),
        master_id_b  TEXT NOT NULL REFERENCES master_patients(id),
        score        REAL NOT NULL,
        status       TEXT DEFAULT 'pending',
        reviewed_by  TEXT,
        reviewedat   TEXT,
        createdat    TEXT DEFAULT (NOW()::TEXT),
        UNIQUE(master_id_a, master_id_b)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id           SERIAL PRIMARY KEY,
        master_id    TEXT,
        action       TEXT NOT NULL,
        performed_by TEXT,
        details      TEXT,
        createdat    TEXT DEFAULT (NOW()::TEXT)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mp_mrn      ON master_patients(mrn)",
    "CREATE INDEX IF NOT EXISTS idx_mp_name     ON master_patients(lastname, firstname)",
    "CREATE INDEX IF NOT EXISTS idx_mp_status   ON master_patients(status)",
    "CREATE INDEX IF NOT EXISTS idx_xref_sys    ON cross_references(system, system_id)",
    "CREATE INDEX IF NOT EXISTS idx_xref_master ON cross_references(master_id)",
    "CREATE INDEX IF NOT EXISTS idx_mc_status   ON match_candidates(status)",
    "CREATE INDEX IF NOT EXISTS idx_audit_mid   ON audit_log(master_id)",
]


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in _SCHEMA_STMTS:
            cur.execute(stmt)


def new_id():
    return str(uuid.uuid4())


def row_to_dict(row):
    return dict(row) if row else None


def rows_to_list(rows):
    return [dict(r) for r in rows]
