import os, sys, pytest
from fastapi.testclient import TestClient

SERVICE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'fhir-bridge'))
if SERVICE not in sys.path:
    sys.path.insert(0, SERVICE)

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("FHIR_SERVER_URL", "http://hapi:8080/fhir")
    monkeypatch.setenv("FHIR_ENABLED", "false")
    monkeypatch.setenv("ORTHANC_URL", "http://orthanc:8042")
    for mod in [k for k in sys.modules if k in ('main', 'routers', 'database') or k.startswith('routers.')
                or k.startswith('translators.')]:
        del sys.modules[mod]
    # Ensure fhir-bridge is at front of path before importing
    if SERVICE in sys.path:
        sys.path.remove(SERVICE)
    sys.path.insert(0, SERVICE)
    from main import app
    return TestClient(app)
