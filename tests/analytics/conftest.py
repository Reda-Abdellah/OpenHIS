import os, sys, json, tempfile, datetime, pytest
from pathlib import Path

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh analytics database for each test"""
    analytics_path = str(Path(__file__).parent.parent.parent / "services" / "analytics")
    test_db = "/tmp/test_analytics.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('analytics_', 'routers', 'scheduler', 'collector')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure analytics is first in path
    if analytics_path in sys.path:
        sys.path.remove(analytics_path)
    sys.path.insert(0, analytics_path)
    
    # Remove old test db
    if os.path.exists(test_db):
        os.remove(test_db)
    
    # Setup environment for analytics service
    os.environ['DB_PATH'] = test_db
    os.environ['EHR_URL'] = 'http://localhost:19999/api'   # unreachable — OK
    os.environ['LIS_URL'] = 'http://localhost:19999/api'
    os.environ['RIS_URL'] = 'http://localhost:19999/api'
    os.environ['AI_CONTROLLER_URL'] = 'http://localhost:19999/api'
    os.environ['MPI_URL'] = 'http://localhost:19999/api'
    os.environ['COLLECT_INTERVAL_MIN'] = '99999'   # disable auto-scheduler in tests
    
    from database import init_db
    init_db()
    
    yield
    
    # Cleanup
    if os.path.exists(test_db):
        os.remove(test_db)


def _seed(domain: str, data: dict, ts: str = None):
    """Seed test data directly into analytics database"""
    ts = ts or datetime.datetime.utcnow().isoformat(timespec='seconds')
    from database import get_db
    with get_db() as db:
        db.execute(
            "INSERT INTO snapshots(domain,data,captured_at) VALUES(?,?,?)",
            (domain, json.dumps(data), ts)
        )


@pytest.fixture
def client(fresh_db):
    """Setup FastAPI test client for analytics service"""
    from main import app
    from fastapi.testclient import TestClient
    
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def seed():
    """Fixture to seed test data directly."""
    return _seed
