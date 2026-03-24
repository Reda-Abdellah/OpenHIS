import os, sys, tempfile, datetime, pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh patient-portal database for each test"""
    portal_path = str(Path(__file__).parent.parent.parent / "patient-portal")
    test_db = "/tmp/test_portal.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('portal_', 'routers', 'proxy', 'auth')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure patient-portal is first in path
    if portal_path in sys.path:
        sys.path.remove(portal_path)
    sys.path.insert(0, portal_path)
    
    # Remove old test db
    if os.path.exists(test_db):
        os.remove(test_db)
    
    # Setup environment for patient-portal service
    os.environ['DB_PATH'] = test_db
    os.environ['EHR_URL'] = 'http://localhost:19999/api'
    os.environ['RIS_URL'] = 'http://localhost:19999/api'
    os.environ['SESSION_TTL_HOURS'] = '24'
    
    from database import init_db
    init_db()
    
    yield
    
    # Cleanup
    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    """Setup FastAPI test client for patient-portal service"""
    from main import app
    
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def patient_session(client):
    """Create a patient session for testing"""
    from auth import create_session
    session_token = create_session("P-001", "MRN-001", "Test Patient")
    return {"Authorization": f"Bearer {session_token}"}



@pytest.fixture
def auth_headers(fresh_db):
    from auth import create_session
    token = create_session("P-001", "MRN-001", "Jane Doe")
    return {"Authorization": f"Bearer {token}"}
