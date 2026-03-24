import os, sys, tempfile, pytest
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh pharmacy database for each test"""
    pharmacy_path = str(Path(__file__).parent.parent.parent / "services" / "pharmacy")
    test_db = "/tmp/test_pharmacy.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('pharmacy_', 'routers')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure pharmacy is first in path
    if pharmacy_path in sys.path:
        sys.path.remove(pharmacy_path)
    sys.path.insert(0, pharmacy_path)
    
    # Remove old test db
    if os.path.exists(test_db):
        os.remove(test_db)
    
    # Setup environment for pharmacy service
    os.environ['DB_PATH'] = test_db
    
    from database import init_db
    init_db()
    
    yield
    
    # Cleanup
    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    """Setup FastAPI test client for pharmacy service"""
    from main import app
    
    with TestClient(app) as c:
        yield c
