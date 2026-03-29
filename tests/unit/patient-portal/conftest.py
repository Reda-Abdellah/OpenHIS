import os, sys, datetime, pytest
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh patient-portal database for each test"""
    portal_path = str(Path(__file__).parent.parent.parent / "services" / "patient-portal")
    test_db = "/tmp/test_portal.db"

    mods_to_remove = [m for m in sys.modules.keys()
                      if m.startswith(('portal_', 'routers', 'proxy', 'auth'))
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass

    if portal_path in sys.path:
        sys.path.remove(portal_path)
    sys.path.insert(0, portal_path)

    if os.path.exists(test_db):
        os.remove(test_db)

    os.environ['DB_PATH']          = test_db
    # Point upstreams at unique test URLs so respx can intercept them
    os.environ['OPENMRS_URL']      = 'http://openmrs-test:9999'
    os.environ['OPENMRS_USER']     = 'admin'
    os.environ['OPENMRS_PASS']     = 'Admin123'
    os.environ['OPENELIS_URL']     = 'http://openelis-test:9999'
    os.environ['OPENELIS_USER']    = 'admin'
    os.environ['OPENELIS_PASS']    = 'adminADMIN!'
    os.environ['RIS_URL']          = 'http://localhost:19999/api'
    os.environ['SESSION_TTL_HOURS'] = '24'

    from database import init_db
    init_db()

    yield

    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def auth_headers(fresh_db):
    from auth import create_session
    token = create_session("P-001", "MRN-001", "Jane Doe")
    return {"Authorization": f"Bearer {token}"}
