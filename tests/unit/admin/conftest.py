import os
import sys
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

ADMIN_PATH = str(Path(__file__).parent.parent.parent / "services" / "admin")


@pytest.fixture(autouse=True)
def admin_env(tmp_path):
    """Set up a clean in-memory SQLite database and required env vars for each test."""
    mods_to_remove = [
        m for m in list(sys.modules)
        if m.startswith(("routers", "routers."))
        or m in (
            "main", "database", "security", "log_config", "jwt_auth",
            "routers.auth", "routers.users", "routers.registry",
            "routers.profiles", "routers.audit", "routers.config",
            "routers.announcements", "routers.platform", "routers.events",
            "routers.services",
        )
    ]
    for mod in mods_to_remove:
        sys.modules.pop(mod, None)

    if ADMIN_PATH in sys.path:
        sys.path.remove(ADMIN_PATH)
    sys.path.insert(0, ADMIN_PATH)

    db_file = str(tmp_path / "admin_test.db")
    os.environ["DB_PATH"]      = db_file
    os.environ["ADMIN_USER"]   = "admin"
    os.environ["ADMIN_PASS"]   = "test_password_123"
    os.environ["ROOT_PATH"]    = ""
    os.environ["REQUIRE_JWT"]  = "false"
    os.environ["KEYCLOAK_URL"] = ""
    os.environ["LOG_FORMAT"]   = "text"
    os.environ["REDIS_URL"]    = ""

    import database
    database.DBPATH = db_file
    database.init_db()

    yield

    for mod in list(sys.modules):
        if mod.startswith(("routers", "routers.")) or mod in (
            "main", "database", "security", "log_config", "jwt_auth",
        ):
            sys.modules.pop(mod, None)


@pytest.fixture
def client(admin_env):
    from main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def auth_headers(client):
    """Return Authorization header for a valid admin session."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "test_password_123"})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}
