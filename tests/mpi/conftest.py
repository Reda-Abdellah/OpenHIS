import os, sys, tempfile, pytest
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def fresh_db():
    """Setup fresh mpi database for each test"""
    mpi_path = str(Path(__file__).parent.parent.parent / "mpi")
    test_db = "/tmp/test_mpi.db"
    
    # Clear cached modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('mpi_', 'routers', 'matcher')) 
                      or m in ('main', 'database')]
    for mod in mods_to_remove:
        try:
            del sys.modules[mod]
        except KeyError:
            pass
    
    # Ensure mpi is first in path
    if mpi_path in sys.path:
        sys.path.remove(mpi_path)
    sys.path.insert(0, mpi_path)
    
    # Remove old test db
    if os.path.exists(test_db):
        os.remove(test_db)
    
    # Setup environment for mpi service
    os.environ['DB_PATH'] = test_db
    
    from database import init_db
    init_db()
    
    yield
    
    # Cleanup
    if os.path.exists(test_db):
        os.remove(test_db)


@pytest.fixture
def client(fresh_db):
    """Setup FastAPI test client for mpi service"""
    from main import app
    
    with TestClient(app) as c:
        yield c
