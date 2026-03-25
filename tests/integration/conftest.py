"""
Shared fixtures for cross-service integration tests.

Strategy: load one service at a time with a fresh module namespace,
then mock outbound HTTP calls with respx to capture/verify payloads.
"""
import os, sys, pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


def _clear_service_modules():
    """Remove any previously-loaded service modules so the next import is fresh."""
    to_remove = [k for k in sys.modules
                 if k in ('main', 'database', 'security',
                          'handlers', 'mllp', 'parser', 'builder',
                          'cdss_engine', 'presets', 'dicom_factory',
                          'routers', 'translators')  # bare package names too
                 or k.startswith('routers.')
                 or k.startswith('translators.')]
    for mod in to_remove:
        del sys.modules[mod]


def _load(service_name: str, env: dict):
    """
    Load a service fresh and return its FastAPI app.
    Call _clear_service_modules() before this.
    """
    svc_path = str(ROOT / "services" / service_name)

    # Remove ALL service paths from sys.path, then add the target at front.
    # This prevents leftover paths from a previous service causing wrong imports.
    services_root = str(ROOT / "services")
    sys.path[:] = [p for p in sys.path
                   if not (p.startswith(services_root) and p != svc_path)]
    if svc_path in sys.path:
        sys.path.remove(svc_path)
    sys.path.insert(0, svc_path)

    for k, v in env.items():
        os.environ[k] = v
    from main import app  # noqa: PLC0415
    try:
        from database import init_db  # noqa: PLC0415
        init_db()
    except (ImportError, Exception):
        # Some services (fhir-bridge) have no database; others may fail with
        # PermissionError on default DB paths — ignore either way.
        pass
    return app


# ─────────────────────────── EHR ───────────────────────────────────────────

@pytest.fixture
def ehr_client(tmp_path, monkeypatch):
    db = str(tmp_path / "ehr.db")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("DBPATH", db)
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("FHIR_BRIDGE_URL", "http://fhir-bridge:8005")
    _clear_service_modules()
    app = _load("ehr", {
        "DB_PATH": db, "DBPATH": db, "ROOT_PATH": "",
        "FHIR_BRIDGE_URL": "http://fhir-bridge:8005",
    })
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def ehr_patient(ehr_client, tmp_path):
    """A pre-created patient in the EHR (FHIR bridge call is mocked away)."""
    import respx, httpx, uuid
    mrn = f"INT-{uuid.uuid4().hex[:8].upper()}"
    with respx.mock:
        respx.post("http://fhir-bridge:8005/api/events/patient-created").mock(
            return_value=httpx.Response(200, json={"status": "queued"})
        )
        r = ehr_client.post("/api/patients", json={
            "mrn": mrn, "first_name": "Integration",
            "last_name": "Test", "birth_date": "1980-06-15", "sex": "M"
        })
    assert r.status_code == 201, r.text
    return r.json()


# ─────────────────────────── FHIR Bridge ───────────────────────────────────

@pytest.fixture
def fhir_client(monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("FHIR_SERVER_URL", "http://hapi:8080/fhir")
    monkeypatch.setenv("FHIR_ENABLED", "false")
    monkeypatch.setenv("RIS_URL", "http://ris:8002/api")
    monkeypatch.setenv("LIS_URL", "http://lis:8004/api")
    monkeypatch.setenv("EHR_URL", "http://ehr:8003/api")
    monkeypatch.setenv("ORTHANC_URL", "http://orthanc:8042")
    monkeypatch.setenv("HL7_URL", "")
    _clear_service_modules()
    app = _load("fhir-bridge", {
        "ROOT_PATH": "", "FHIR_SERVER_URL": "http://hapi:8080/fhir",
        "FHIR_ENABLED": "false",
        "RIS_URL": "http://ris:8002/api",
        "LIS_URL": "http://lis:8004/api",
        "EHR_URL": "http://ehr:8003/api",
        "ORTHANC_URL": "http://orthanc:8042",
        "HL7_URL": "",
    })
    from fastapi.testclient import TestClient
    return TestClient(app)


# ─────────────────────────── RIS ───────────────────────────────────────────

@pytest.fixture
def ris_client(tmp_path, monkeypatch):
    db = str(tmp_path / "ris.db")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("DBPATH", db)
    monkeypatch.setenv("ROOT_PATH", "")
    _clear_service_modules()
    app = _load("ris", {"DB_PATH": db, "DBPATH": db, "ROOT_PATH": ""})
    from fastapi.testclient import TestClient
    return TestClient(app)


# ─────────────────────────── LIS ───────────────────────────────────────────

@pytest.fixture
def lis_client(tmp_path, monkeypatch):
    db = str(tmp_path / "lis.db")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("DBPATH", db)
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("FHIR_BRIDGE_URL", "")
    _clear_service_modules()
    app = _load("lis", {
        "DB_PATH": db, "DBPATH": db, "ROOT_PATH": "",
        "FHIR_BRIDGE_URL": "",
    })
    from fastapi.testclient import TestClient
    return TestClient(app)
