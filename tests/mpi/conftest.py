import os, sys, pytest
from pathlib import Path
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh MPI database for each test."""
    mpi_path = str(Path(__file__).parent.parent.parent / "services" / "mpi")
    test_db = "/tmp/test_mpi.db"

    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys()
                      if m.startswith(('routers', 'bus_consumer'))
                      or m in ('main', 'database', 'matcher')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass

    if mpi_path in sys.path:
        sys.path.remove(mpi_path)
    sys.path.insert(0, mpi_path)

    if os.path.exists(test_db):
        os.remove(test_db)

    os.environ['DB_PATH'] = test_db
    os.environ['ROOT_PATH'] = ''
    os.environ['REDIS_URL'] = ''   # disable bus consumer in tests

    from database import init_db
    init_db()

    yield

    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    from main import app
    return TestClient(app)
