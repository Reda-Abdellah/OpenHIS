import os, sys, pytest
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh lis database for each test"""
    lis_path = str(Path(__file__).parent.parent.parent / "lis")
    test_db = "/tmp/test_lis.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('lis_', 'routers')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure lis is first in path
    if lis_path in sys.path:
        sys.path.remove(lis_path)
    sys.path.insert(0, lis_path)
    
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
    """Setup FastAPI test client for lis service"""
    from main import app
    return TestClient(app)

@pytest.fixture
def lab_patient(client):
    r = client.post("/api/lab-patients", json={"mrn": "LAB001", "patient_name": "Jane Lab"})
    return r.json()

@pytest.fixture
def specimen(client, lab_patient):
    r = client.post("/api/specimens", json={
        "patient_id": lab_patient["id"], "specimen_type": "blood"
    })
    return r.json()

@pytest.fixture
def lab_order(client, specimen):
    r = client.post("/api/lab-orders", json={
        "specimen_id": specimen["id"], "test_code": "CBC"
    })
    return r.json()
