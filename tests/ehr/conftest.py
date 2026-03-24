import os, sys, pytest
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh ehr database for each test"""
    ehr_path = str(Path(__file__).parent.parent.parent / "services" / "ehr")
    test_db = "/tmp/test_ehr.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('ehr_', 'routers', 'cdss', 'fhir_composition')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure ehr is first in path
    if ehr_path in sys.path:
        sys.path.remove(ehr_path)
    sys.path.insert(0, ehr_path)
    
    # Remove old test db
    if os.path.exists(test_db):
        os.remove(test_db)
    
    # Setup environment for ehr service
    os.environ['DB_PATH'] = test_db
    os.environ['DBPATH'] = test_db  # compatibility
    os.environ['ROOT_PATH'] = ''
    os.environ['FHIR_BRIDGE_URL'] = ''  # disable outbound HTTP in tests
    os.environ['RIS_URL'] = 'http://localhost:19999/api'
    os.environ['LIS_URL'] = 'http://localhost:19999/api'
    
    # Initialize database with schema only (no seed data for test isolation)
    from database import get_db, SCHEMA
    with get_db() as db:
        db.executescript(SCHEMA)
    
    yield
    
    # Cleanup
    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    """Setup FastAPI test client for ehr service"""
    from main import app
    
    return TestClient(app)

@pytest.fixture
def patient(client):
    r = client.post("/api/patients", json={
        "mrn": "MRN001", "first_name": "Alice", "last_name": "Smith",
        "birth_date": "1990-01-01", "sex": "F"
    })
    assert r.status_code == 201, r.text
    return r.json()
