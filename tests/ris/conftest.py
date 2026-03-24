import os, sys, pytest
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh ris database for each test"""
    ris_path = str(Path(__file__).parent.parent.parent / "services" / "ris")
    test_db = "/tmp/test_ris.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('ris_', 'routers')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure ris is first in path
    if ris_path in sys.path:
        sys.path.remove(ris_path)
    sys.path.insert(0, ris_path)
    
    # Remove old test db
    if os.path.exists(test_db):
        os.remove(test_db)
    
    # Setup environment
    os.environ['DBPATH'] = test_db
    os.environ['DB_PATH'] = test_db
    os.environ['ROOT_PATH'] = ''
    os.environ['FHIR_BRIDGE_URL'] = ''
    
    from database import init_db
    init_db()
    
    yield
    
    # Cleanup
    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    """Setup FastAPI test client for ris service"""
    from main import app
    return TestClient(app)

@pytest.fixture
def patient(client):
    r = client.post("/api/patients", json={"mrn": "RIS001", "patient_name": "Test Patient"})
    assert r.status_code == 201
    return r.json()

@pytest.fixture
def order(client, patient):
    r = client.post("/api/orders", json={
        "patient_id": patient["id"], "modality": "CT",
        "body_part": "CHEST", "priority": "ROUTINE",
        "requesting_physician": "Dr. Grey"
    })
    assert r.status_code == 201
    return r.json()
