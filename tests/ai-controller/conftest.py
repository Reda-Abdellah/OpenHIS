import os, sys, pytest
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh ai-controller database for each test"""
    ai_path = str(Path(__file__).parent.parent.parent / "services" / "ai-controller")
    test_db = "/tmp/test_ai_controller.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('ai_', 'routers', 'orthanc_client', 'runner')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure ai-controller is first in path
    if ai_path in sys.path:
        sys.path.remove(ai_path)
    sys.path.insert(0, ai_path)
    
    # Remove old test db
    if os.path.exists(test_db):
        os.remove(test_db)
    
    # Setup environment
    os.environ['DBPATH'] = test_db
    os.environ['DB_PATH'] = test_db
    os.environ['ROOT_PATH'] = ''
    os.environ['ORTHANC_URL'] = 'http://orthanc:8042'
    os.environ['JOBS_DATA_DIR'] = '/tmp/ai_jobs'
    os.environ['FHIR_BRIDGE_URL'] = ''
    
    from database import init_db
    init_db()
    
    yield
    
    # Cleanup
    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    """Setup FastAPI test client for ai-controller service"""
    from main import app
    return TestClient(app)
