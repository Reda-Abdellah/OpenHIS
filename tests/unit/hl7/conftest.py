import os, sys, pytest
from pathlib import Path

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh hl7 database for each test"""
    hl7_path = str(Path(__file__).parent.parent.parent.parent / "services" / "hl7")
    # Per-process path: concurrent pytest runs (e.g. parallel CI shards or
    # agents) sharing a fixed /tmp/test_hl7.db corrupt each other's WAL.
    test_db = f"/tmp/test_hl7_{os.getpid()}.db"

    mods_to_remove = [m for m in sys.modules.keys()
                      if m.startswith(('hl7_', 'routers', 'handlers', 'mllp',
                                       'parser', 'builder', 'bus_consumer'))
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass

    if hl7_path in sys.path:
        sys.path.remove(hl7_path)
    sys.path.insert(0, hl7_path)

    if os.path.exists(test_db):
        os.remove(test_db)

    os.environ['DB_PATH']         = test_db
    os.environ['MLLP_ENABLED']    = 'false'        # don't bind TCP port in tests
    # Point OpenMRS at an unreachable port — handler errors are best-effort
    os.environ['OPENMRS_URL']     = 'http://localhost:19999'
    os.environ['OPENMRS_USER']    = 'admin'
    os.environ['OPENMRS_PASS']    = 'Admin123'

    from database import init_db
    init_db()

    yield

    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    from main import app
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
