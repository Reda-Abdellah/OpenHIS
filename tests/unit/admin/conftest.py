import os
import sys
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

ADMIN_PATH = str(Path(__file__).parent.parent.parent.parent / "services" / "admin")


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
    os.environ["ROOT_PATH"]    = ""
    os.environ["KEYCLOAK_URL"] = ""   # DEV_MODE bypasses Keycloak calls
    os.environ["LOG_FORMAT"]   = "text"
    os.environ["REDIS_URL"]    = ""
    # NOTE: ADMIN_USER, ADMIN_PASS, REQUIRE_JWT are not read by admin v2.0
    # (Keycloak-only auth) and have been removed.

    import database
    database.DBPATH = db_file
    database.init_db()

    # Replicate the lifespan startup seed so registry tests see base services.
    # (TestClient without a `with` block does not run the lifespan.)
    from routers.registry import seed_base_services
    seed_base_services()

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
    """Return Authorization header for tests.

    DEV_MODE=true is set in the root conftest so require_token returns dev
    claims regardless of token content — any Bearer value is accepted.
    """
    return {"Authorization": "Bearer dev-test-token"}
