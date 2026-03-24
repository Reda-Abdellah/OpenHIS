import os, sys, tempfile, pytest
from pathlib import Path

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh hl7 database for each test"""
    hl7_path = str(Path(__file__).parent.parent.parent / "hl7")
    test_db = "/tmp/test_hl7.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('hl7_', 'routers', 'handlers', 'mllp', 'parser', 'builder')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure hl7 is first in path
    if hl7_path in sys.path:
        sys.path.remove(hl7_path)
    sys.path.insert(0, hl7_path)
    
    # Remove old test db
    if os.path.exists(test_db):
        os.remove(test_db)
    
    # Setup environment for hl7 service
    os.environ['DB_PATH'] = test_db
    os.environ['MLLP_ENABLED'] = 'false'       # don't bind TCP port in tests
    os.environ['EHR_URL'] = 'http://localhost:19999/api'
    os.environ['MPI_URL'] = 'http://localhost:19999/api'
    
    from database import init_db
    init_db()
    
    yield
    
    # Cleanup
    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    """Setup FastAPI test client for hl7 service"""
    from main import app
    from fastapi.testclient import TestClient
    
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
