"""
Shared fixtures for cross-service integration tests.

Strategy: load one service at a time with a fresh module namespace,
then mock outbound HTTP calls with respx to capture/verify payloads.
"""
import asyncio
import os, sys, pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _fresh_event_loop():
    """Give every integration test a usable current event loop.

    Tests here drive adapter coroutines via
    ``asyncio.get_event_loop().run_until_complete(...)``. When the unit
    suite runs first in the same pytest invocation, pytest-asyncio leaves
    the main thread with no current loop and Python 3.11 raises
    ``RuntimeError: There is no current event loop``.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()
    asyncio.set_event_loop(None)

ROOT = Path(__file__).parent.parent.parent
HUB_PATH = str(ROOT / "services" / "integration-hub")
AI_PATH  = str(ROOT / "services" / "ai-controller")


def _clear_service_modules():
    to_remove = [k for k in sys.modules
                 if k in ('main', 'database',
                          'handlers', 'mllp', 'parser', 'builder',
                          'dicom_factory', 'routers', 'translators')
                 or k.startswith('routers.')
                 or k.startswith('translators.')]
    for mod in to_remove:
        del sys.modules[mod]


def _clear_hub_modules():
    for mod in list(sys.modules.keys()):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


def _clear_ai_modules():
    to_remove = [m for m in sys.modules
                 if m.startswith(('routers', 'bus_consumer', 'runner', 'orthanc_client'))
                 or m in ('main', 'database')]
    for mod in to_remove:
        del sys.modules[mod]


def _load(service_name: str, env: dict):
    svc_path = str(ROOT / "services" / service_name)
    services_root = str(ROOT / "services")
    sys.path[:] = [p for p in sys.path
                   if not (p.startswith(services_root) and p != svc_path)]
    if svc_path in sys.path:
        sys.path.remove(svc_path)
    sys.path.insert(0, svc_path)
    for k, v in env.items():
        os.environ[k] = v
    from main import app
    try:
        from database import init_db
        init_db()
    except (ImportError, Exception):
        pass
    return app


# ─────────────────────────── RIS ───────────────────────────────────────────

@pytest.fixture
def ris_client(tmp_path, monkeypatch):
    db = str(tmp_path / "ris.db")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("OPENMRS_URL",     "http://localhost:19999")
    monkeypatch.setenv("OPENMRS_USER",    "admin")
    monkeypatch.setenv("OPENMRS_PASS",    "admin")
    monkeypatch.setenv("POLL_INTERVAL_S", "99999")
    _clear_service_modules()
    # clear openmrs_sync so it re-reads DB_PATH from env
    for mod in list(sys.modules.keys()):
        if mod == "openmrs_sync":
            del sys.modules[mod]
    app = _load("ris", {"DB_PATH": db, "ROOT_PATH": "",
                        "OPENMRS_URL": "http://localhost:19999",
                        "POLL_INTERVAL_S": "99999"})
    from fastapi.testclient import TestClient
    return TestClient(app)


# ─────────────────────────── Integration Hub ───────────────────────────────

@pytest.fixture
def hub_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB_PATH",   str(tmp_path / "hub-audit.db"))
    monkeypatch.setenv("ROOT_PATH",       "")
    monkeypatch.setenv("OPENMRS_URL",     "http://openmrs-int-test:9997")
    monkeypatch.setenv("OPENELIS_URL",    "http://openelis-int-test:9997")
    monkeypatch.setenv("ODOO_URL",        "http://odoo-int-test:9997")
    monkeypatch.setenv("ODOO_DB",         "odoo")
    monkeypatch.setenv("POLL_INTERVAL_S", "99999")

    # Scrub other service paths: several services ship an `app/` package,
    # and a leftover unit-suite path would shadow the hub's `app.main`
    # when both suites run in one pytest invocation.
    services_root = str(ROOT / "services")
    sys.path[:] = [p for p in sys.path
                   if not (p.startswith(services_root) and p != HUB_PATH)]
    if HUB_PATH in sys.path:
        sys.path.remove(HUB_PATH)
    sys.path.insert(0, HUB_PATH)
    _clear_hub_modules()

    from app.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


# ─────────────────────────── AI Controller ─────────────────────────────────

@pytest.fixture
def ai_client(tmp_path, monkeypatch):
    db = str(tmp_path / "ai.db")
    jobs_dir = str(tmp_path / "ai_jobs")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("ORTHANC_URL",    "http://localhost:19999")
    monkeypatch.setenv("JOBS_DATA_DIR",  jobs_dir)
    monkeypatch.setenv("FHIR_BRIDGE_URL", "")
    monkeypatch.setenv("REDIS_URL",      "")
    monkeypatch.setenv("OPENELIS_URL",   "")

    if AI_PATH in sys.path:
        sys.path.remove(AI_PATH)
    sys.path.insert(0, AI_PATH)
    _clear_ai_modules()

    from database import init_db
    init_db()
    from main import app
    from fastapi.testclient import TestClient
    return TestClient(app)
