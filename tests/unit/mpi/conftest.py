"""
MPI test conftest.

The MPI service uses PostgreSQL with PostgreSQL-specific DDL (SERIAL, RETURNING,
ON CONFLICT, NOW()::TEXT) — there is no in-memory equivalent. These tests therefore
require a reachable PostgreSQL instance with a `mpi_test` database. Per DEF-003 in
docs/task_planning/test-defect-report-2026-04-14.md, the suite degrades gracefully:

  - If MPI_DATABASE_URL is reachable → tests run against it (DDL is wiped per test)
  - If unreachable → DB-bound tests are skipped at collection time
  - Pure-logic tests (e.g. test_matcher.py) run unconditionally

To run locally with Docker:
    docker compose -f compose/base.yml up -d postgres
    psql -h localhost -U postgres -c "CREATE DATABASE mpi_test OWNER mpi;"

Override the DSN via the `MPI_DATABASE_URL` env var (e.g. for CI sidecars).
"""
import os
import sys
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

MPI_PATH = str(Path(__file__).parent.parent.parent.parent / "services" / "mpi")

_DEFAULT_TEST_DB = "postgresql://mpi:mpi@localhost:5432/mpi_test"


def _postgres_reachable(dsn: str) -> tuple[bool, str]:
    """Return (reachable, reason). Reason is empty when reachable."""
    try:
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=2)
        conn.close()
        return True, ""
    except Exception as exc:
        return False, f"PostgreSQL not reachable at {dsn}: {exc.__class__.__name__}"


_TEST_DB_URL = os.environ.get("MPI_DATABASE_URL", _DEFAULT_TEST_DB)
_PG_OK, _PG_REASON = _postgres_reachable(_TEST_DB_URL)


# Skip marker that DB-bound tests opt into.
requires_pg = pytest.mark.skipif(not _PG_OK, reason=_PG_REASON or "PostgreSQL not available")


@pytest.fixture(autouse=True)
def fresh_db(request):
    """
    Set up a clean MPI schema in the test PostgreSQL database for each test.

    Tests that don't need the DB should declare `pytestmark = pytest.mark.no_db`
    or simply not use the `client` / `db` fixtures. This fixture is autouse but
    cheaply no-ops when the test is marked `no_db` or when Postgres is unavailable.
    """
    if request.node.get_closest_marker("no_db"):
        yield
        return

    if not _PG_OK:
        pytest.skip(_PG_REASON)

    # Clear cached MPI modules so each test gets a fresh import (env-driven config)
    mods_to_remove = [
        m for m in list(sys.modules)
        if m.startswith(("routers", "bus_consumer"))
        or m in ("main", "database", "matcher", "log_config", "jwt_auth", "bus")
    ]
    for mod in mods_to_remove:
        sys.modules.pop(mod, None)

    if MPI_PATH in sys.path:
        sys.path.remove(MPI_PATH)
    sys.path.insert(0, MPI_PATH)

    os.environ["MPI_DATABASE_URL"] = _TEST_DB_URL
    os.environ["ROOT_PATH"] = ""
    os.environ["REDIS_URL"] = ""      # disable bus consumer in tests
    os.environ["LOG_FORMAT"] = "text"

    import psycopg2
    import psycopg2.extras

    def _wipe():
        conn = psycopg2.connect(_TEST_DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        cur = conn.cursor()
        cur.execute(
            "DROP TABLE IF EXISTS audit_log, match_candidates, "
            "cross_references, master_patients CASCADE"
        )
        conn.commit()
        conn.close()

    _wipe()

    from database import init_db
    init_db()

    yield

    _wipe()


@pytest.fixture
def client(fresh_db):
    from main import app
    return TestClient(app)


@pytest.fixture
def db(fresh_db):
    """Direct DB handle for arrange-stage seeding inside tests."""
    from database import get_db
    return get_db


# Custom markers — registered to suppress PytestUnknownMarkWarning.
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "no_db: test does not need PostgreSQL (skips fresh_db setup)"
    )
