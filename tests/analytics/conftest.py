import os, sys, json, datetime, pytest
from pathlib import Path

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh analytics database for each test"""
    analytics_path = str(Path(__file__).parent.parent.parent / "services" / "analytics")
    test_db = "/tmp/test_analytics.db"

    mods_to_remove = [m for m in sys.modules.keys()
                      if m.startswith(('analytics_', 'routers', 'scheduler', 'collector'))
                      or m in ('main', 'database', 'bus_consumer')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass

    if analytics_path in sys.path:
        sys.path.remove(analytics_path)
    sys.path.insert(0, analytics_path)

    if os.path.exists(test_db):
        os.remove(test_db)

    os.environ['DB_PATH'] = test_db
    # Point all upstreams at an unreachable port — no real HTTP in unit tests
    os.environ['OPENMRS_URL']          = 'http://localhost:19999'
    os.environ['OPENMRS_USER']         = 'admin'
    os.environ['OPENMRS_PASS']         = 'Admin123'
    os.environ['OPENELIS_URL']         = 'http://localhost:19999'
    os.environ['OPENELIS_USER']        = 'admin'
    os.environ['OPENELIS_PASS']        = 'adminADMIN!'
    os.environ['RIS_URL']              = 'http://localhost:19999/api'
    os.environ['AI_CONTROLLER_URL']    = 'http://localhost:19999/api'
    os.environ['COLLECT_INTERVAL_MIN'] = '99999'   # disable auto-scheduler

    from database import init_db
    init_db()

    yield

    if os.path.exists(test_db):
        os.remove(test_db)


def _seed(domain: str, data: dict, ts: str = None):
    ts = ts or datetime.datetime.utcnow().isoformat(timespec='seconds')
    from database import get_db
    with get_db() as db:
        db.execute(
            "INSERT INTO snapshots(domain,data,captured_at) VALUES(?,?,?)",
            (domain, json.dumps(data), ts)
        )


@pytest.fixture
def client(fresh_db):
    from main import app
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def seed():
    return _seed
