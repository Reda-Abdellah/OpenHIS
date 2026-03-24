import os, sys, tempfile, pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Setup fresh admin database without a full HTTP client."""
    db = str(tmp_path / "admin_test.db")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("SESSION_TTL_HOURS", "12")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "admin123")
    mods_to_remove = [m for m in sys.modules.keys()
                      if m.startswith(('admin_', 'routers', 'security', 'database'))
                      or m in ('main', 'auth', 'users', 'services', 'config', 'announcements', 'audit')]
    for mod in mods_to_remove:
        del sys.modules[mod]
    admin_path = str(Path(__file__).parent.parent.parent / "admin")
    if admin_path in sys.path:
        sys.path.remove(admin_path)
    sys.path.insert(0, admin_path)
    from database import init_db
    import main as main_module
    init_db()
    main_module._seed_default_admin()
    yield


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Setup FastAPI test client for admin service"""
    db = str(tmp_path / "admin_test.db")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("SESSION_TTL_HOURS", "12")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "admin123")
    
    # Clear any cached admin-related modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('admin_', 'routers', 'security', 'database')) 
                      or m in ('main', 'auth', 'users', 'services', 'config', 'announcements', 'audit')]
    for mod in mods_to_remove:
        del sys.modules[mod]
    
    # Put admin service at front of path
    admin_path = str(Path(__file__).parent.parent.parent / "admin")
    if admin_path in sys.path:
        sys.path.remove(admin_path)
    sys.path.insert(0, admin_path)
    
    # Now import from admin
    from main import app
    from database import init_db
    
    init_db()
    
    # Seed default admin user
    import main as main_module
    main_module._seed_default_admin()
    
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture  
def auth(client):
    """Fixture to get admin auth headers"""
    from security import create_admin_session
    from database import get_db
    
    with get_db() as db:
        row = db.execute("SELECT * FROM admin_users WHERE username='admin'").fetchone()
    
    token = create_admin_session(row['id'], 'admin')
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(tmp_path, monkeypatch):
    """Fixture to get admin token"""
    db = str(tmp_path / "admin_test.db")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("SESSION_TTL_HOURS", "12")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "admin123")
    
    # Clear any cached admin-related modules
    mods_to_remove = [m for m in sys.modules.keys() 
                      if m.startswith(('admin_', 'routers', 'security', 'database')) 
                      or m in ('main', 'auth', 'users', 'services', 'config', 'announcements', 'audit')]
    for mod in mods_to_remove:
        del sys.modules[mod]
    
    # Put admin service at front of path
    admin_path = str(Path(__file__).parent.parent.parent / "admin")
    if admin_path in sys.path:
        sys.path.remove(admin_path)
    sys.path.insert(0, admin_path)
    
    from main import app
    from database import init_db
    from security import create_admin_session
    from database import get_db
    
    init_db()
    import main as main_module
    main_module._seed_default_admin()
    
    with get_db() as db:
        row = db.execute("SELECT * FROM admin_users WHERE username='admin'").fetchone()
    
    return create_admin_session(row['id'], 'admin')


@pytest.fixture
def auth(token):
    return {"Authorization": f"Bearer {token}"}
