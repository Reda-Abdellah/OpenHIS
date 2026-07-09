"""
T-06 — integration-hub event-ingest role gates, exercised with REAL auth.

The rest of this directory runs with DEV_MODE=true (require_token returns
dev claims carrying the admin role, so every gate is open). Here we boot the
hub via the tests/auth harness instead: DEV_MODE=false, RS256 tokens minted
against an in-memory JWKS, so each newly gated endpoint is asserted for the
full 401 / 403 / 2xx triple:

  POST /api/events/report-final               → radiologist | admin
  POST /api/events/dicom-stored               → internal-sync | admin
  POST /api/events/ai-job-completed           → internal-sync | admin
  POST /api/atomfeed/trigger                  → admin
  GET  /api/context/diagnostic-report/{oe_id} → internal-sync | admin

GET /api/atomfeed/status stays token-only (any valid token).
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_AUTH_DIR = str(Path(__file__).resolve().parents[2] / "auth")
if _AUTH_DIR not in sys.path:
    sys.path.insert(0, _AUTH_DIR)

import harness  # noqa: E402  (tests/auth/harness.py)


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    """Integration-hub app booted with real JWT validation enforced."""
    tmp = str(tmp_path_factory.mktemp("hub_auth_gates"))
    env = {
        "AUDIT_DB_PATH":   f"{tmp}/hub-audit.db",
        "ROOT_PATH":       "",
        "OPENMRS_URL":     "http://openmrs-auth-test:9997",
        "OPENELIS_URL":    "http://openelis-auth-test:9997",
        "ODOO_URL":        "http://odoo-auth-test:9997",
        "ODOO_DB":         "odoo",
        "POLL_INTERVAL_S": "99999",
    }
    with harness.isolated_service(
        "integration-hub", app_module="app.main", env=env
    ) as app:
        yield TestClient(app, raise_server_exceptions=False)


# Payloads chosen so the queued background handler returns early / degrades
# gracefully (no instanceId / job_id → early return; audit.log_event swallows
# its own errors), keeping the 2xx cases fast and network-free.
EVENT_GATES = [
    pytest.param("/api/events/report-final", {}, ("radiologist",),
                 id="report-final-radiologist"),
    pytest.param("/api/events/report-final", {}, ("admin",),
                 id="report-final-admin"),
    pytest.param("/api/events/dicom-stored", {}, ("internal-sync",),
                 id="dicom-stored-internal-sync"),
    pytest.param("/api/events/dicom-stored", {}, ("admin",),
                 id="dicom-stored-admin"),
    pytest.param("/api/events/ai-job-completed", {}, ("internal-sync",),
                 id="ai-job-completed-internal-sync"),
    pytest.param("/api/events/ai-job-completed", {}, ("admin",),
                 id="ai-job-completed-admin"),
]


@pytest.mark.parametrize("path,payload,granted_roles", EVENT_GATES)
def test_event_ingest_requires_token(hub, path, payload, granted_roles):
    r = hub.post(path, json=payload)
    assert r.status_code == 401, (
        f"POST {path} without a token must return 401, got {r.status_code}"
    )


@pytest.mark.parametrize("path,payload,granted_roles", EVENT_GATES)
def test_event_ingest_rejects_wrong_role(hub, path, payload, granted_roles):
    r = hub.post(path, json=payload, headers=harness.auth_header(["nurse"]))
    assert r.status_code == 403, (
        f"POST {path} with a nurse-only token must return 403, got {r.status_code}"
    )


@pytest.mark.parametrize("path,payload,granted_roles", EVENT_GATES)
def test_event_ingest_accepts_granted_role(hub, path, payload, granted_roles):
    r = hub.post(path, json=payload, headers=harness.auth_header(granted_roles))
    assert r.status_code == 200, (
        f"POST {path} with roles {granted_roles} must return 200, got {r.status_code}"
    )
    assert r.json() == {"status": "queued"}


def test_radiologist_cannot_post_machine_webhooks(hub):
    """report-final allows radiologist; the machine webhooks must not."""
    for path in ("/api/events/dicom-stored", "/api/events/ai-job-completed"):
        r = hub.post(path, json={}, headers=harness.auth_header(["radiologist"]))
        assert r.status_code == 403, (
            f"POST {path} with a radiologist token must return 403, "
            f"got {r.status_code}"
        )


class TestAtomfeedTriggerGate:
    def test_requires_token(self, hub):
        assert hub.post("/api/atomfeed/trigger").status_code == 401

    def test_rejects_non_admin(self, hub):
        r = hub.post("/api/atomfeed/trigger",
                     headers=harness.auth_header(["clinician", "radiologist"]))
        assert r.status_code == 403

    def test_accepts_admin(self, hub):
        r = hub.post("/api/atomfeed/trigger", headers=harness.auth_header(["admin"]))
        assert r.status_code == 200
        assert r.json() == {"status": "triggered"}

    def test_status_stays_token_only(self, hub):
        """GET /status needs a token but no specific role."""
        assert hub.get("/api/atomfeed/status").status_code == 401
        r = hub.get("/api/atomfeed/status", headers=harness.auth_header(["nurse"]))
        assert r.status_code == 200


class TestContextGates:
    """The hub context surface is machine-only.

    The "gate passed" case relies on the suite's unreachable upstream
    hosts: the read degrades to 404 (DiagnosticReport unavailable) —
    proving the request got past 401/403 into the handler.
    """

    PATH = "/api/context/diagnostic-report/dr-1"

    def test_requires_token(self, hub):
        r = hub.get(self.PATH)
        assert r.status_code == 401, (
            f"GET {self.PATH} without a token must return 401, got {r.status_code}"
        )

    def test_rejects_wrong_role(self, hub):
        r = hub.get(self.PATH, headers=harness.auth_header(["nurse"]))
        assert r.status_code == 403, (
            f"GET {self.PATH} with a nurse-only token must return 403, "
            f"got {r.status_code}"
        )

    def test_rejects_clinical_human_roles(self, hub):
        """Even read-heavy clinical roles are denied — machine surface only."""
        r = hub.get(self.PATH, headers=harness.auth_header(
            ["radiologist", "clinician", "lab-tech"]))
        assert r.status_code == 403

    def test_internal_sync_passes_gate(self, hub):
        r = hub.get(self.PATH, headers=harness.auth_header(["internal-sync"]))
        assert r.status_code == 404  # gate passed; OpenELIS unreachable here

    def test_admin_passes_gate(self, hub):
        r = hub.get(self.PATH, headers=harness.auth_header(["admin"]))
        assert r.status_code == 404  # gate passed; OpenELIS unreachable here
