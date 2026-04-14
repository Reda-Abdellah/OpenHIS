"""
Fixtures for integration-hub unit tests.

The hub is a package (app/) so we add services/integration-hub to sys.path
and import via `from app.main import app`.
"""
import os, sys, pytest
from pathlib import Path

HUB_PATH = str(Path(__file__).parent.parent.parent.parent / "services" / "integration-hub")


def _clear_hub_modules():
    for mod in list(sys.modules.keys()):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB_PATH",          str(tmp_path / "hub-audit.db"))
    monkeypatch.setenv("ROOT_PATH",              "")
    monkeypatch.setenv("OPENMRS_URL",            "http://openmrs-hub-test:9998")
    monkeypatch.setenv("OPENELIS_URL",           "http://openelis-hub-test:9998")
    monkeypatch.setenv("ODOO_URL",               "http://odoo-hub-test:9998")
    monkeypatch.setenv("ODOO_DB",                "odoo")
    monkeypatch.setenv("ODOO_ADMIN_PASS",        "test-odoo-pass")
    monkeypatch.setenv("POLL_INTERVAL_S",        "99999")  # never actually poll

    if HUB_PATH not in sys.path:
        sys.path.insert(0, HUB_PATH)

    _clear_hub_modules()

    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
