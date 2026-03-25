import os, sys, pytest
from fastapi.testclient import TestClient

SERVICE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'simulator'))
if SERVICE not in sys.path:
    sys.path.insert(0, SERVICE)


@pytest.fixture(autouse=True)
def _ensure_simulator_path():
    """Re-add simulator service path before each test (integration tests may remove it)."""
    if SERVICE not in sys.path:
        sys.path.insert(0, SERVICE)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORTHANC_URL", "http://orthanc:8042")
    for mod in [k for k in sys.modules if k in ('main', 'database', 'routers', 'dicom_factory', 'presets')
                or k.startswith('routers.')]:
        del sys.modules[mod]
    if SERVICE in sys.path:
        sys.path.remove(SERVICE)
    sys.path.insert(0, SERVICE)
    from main import app
    return TestClient(app)
