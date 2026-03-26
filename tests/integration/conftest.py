"""
Shared fixtures for cross-service integration tests.

Strategy: load one service at a time with a fresh module namespace,
then mock outbound HTTP calls with respx to capture/verify payloads.
"""
import os, sys, pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
HUB_PATH = str(ROOT / "services" / "integration-hub")


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

    if HUB_PATH not in sys.path:
        sys.path.insert(0, HUB_PATH)
    _clear_hub_modules()

    from app.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)
