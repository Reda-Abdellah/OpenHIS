import os
import sys
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

MPI_PATH = str(Path(__file__).parent.parent.parent / "services" / "mpi")

# Default test database URL — overridden by CI via MPI_DATABASE_URL env var
_DEFAULT_TEST_DB = "postgresql://mpi:mpi@localhost:5432/mpi_test"


@pytest.fixture(autouse=True)
def fresh_db():
    """Set up a clean MPI schema in the test PostgreSQL database for each test."""
    # Clear cached modules so each test gets a fresh import
    mods_to_remove = [
        m for m in list(sys.modules)
        if m.startswith(("routers", "bus_consumer"))
        or m in ("main", "database", "matcher", "log_config", "jwt_auth")
    ]
    for mod in mods_to_remove:
        sys.modules.pop(mod, None)

    if MPI_PATH in sys.path:
        sys.path.remove(MPI_PATH)
    sys.path.insert(0, MPI_PATH)

    test_db_url = os.environ.get("MPI_DATABASE_URL", _DEFAULT_TEST_DB)
    os.environ["MPI_DATABASE_URL"] = test_db_url
    os.environ["ROOT_PATH"] = ""
    os.environ["REDIS_URL"] = ""      # disable bus consumer in tests
    os.environ["REQUIRE_JWT"] = "false"
    os.environ["LOG_FORMAT"] = "text"

    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(test_db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS audit_log, match_candidates, cross_references, master_patients CASCADE
    """)
    conn.commit()
    conn.close()

    from database import init_db
    init_db()

    yield

    conn = psycopg2.connect(test_db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS audit_log, match_candidates, cross_references, master_patients CASCADE
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def client(fresh_db):
    from main import app
    return TestClient(app)
