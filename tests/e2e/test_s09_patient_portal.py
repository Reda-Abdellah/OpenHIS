"""
Scenario 9 — Patient-Portal Card End-to-End

Mirrors SCENARIO 9 in docs/verification_and_validation/v-and-v-scenario.md.

Walks every layer the operator and the patient touch when they click the
"Patient Portal" card on the platform landing page:

  ✅ S9.1 — landing page surfaces the Patient Portal card
  ✅ S9.2 — /patient-portal/ SPA loads and is the right HTML shell
  ✅ S9.3 — /patient-portal/api/health returns ok + service identity
  ✅ S9.4 — /api/auth/login validation: missing fields → 400
  ✅ S9.5 — /api/auth/login with bad credentials → 401 (fail-closed)
  ✅ S9.6 — /api/me with no Bearer token → 401 (fail-closed)
  ✅ S9.7 — /api/auth/validate with no Bearer token → 401
  ✅ S9.8 — /api/auth/logout is idempotent (200 with empty / unknown token)
  ❌ S9.9 — Full session flow (login → /me → logout) — needs an OpenMRS-
            resident patient; currently the OpenMRS demo dataset isn't
            seeded by the integration-hub, so no MRN/DOB pair authenticates.
            Auto-promotes once the hub provisions the test patient in
            OpenMRS or once a deterministic demo MRN/DOB is documented.

The card is the *only* entrypoint a patient ever uses — if the landing
HTML, the SPA shell, the health probe, or any of the auth surfaces
silently regress, every patient session breaks at once. This scenario is
the regression net for that path.
"""
import pytest


pytestmark = pytest.mark.e2e


CARD_HREF      = "/patient-portal/"
CARD_LABEL     = "Patient Portal"
SPA_TITLE      = "Patient Portal"
SERVICE_NAME   = "patient-portal"


class TestS9_PatientPortalDiscovery:
    """Operator perspective: the card is visible on the landing page and the
    SPA it links to actually serves HTML."""

    def test_s9_1_landing_card_present(self, http):
        r = http.get("/")
        assert r.status_code == 200, r.text
        # Card href + visible label must both appear so the test fails if the
        # link is renamed *or* the human-readable label drifts away from the
        # routing.
        assert CARD_HREF in r.text, "patient-portal card href missing from landing page"
        assert CARD_LABEL in r.text, "patient-portal card label missing from landing page"

    def test_s9_2_spa_loads(self, http):
        r = http.get(CARD_HREF)
        assert r.status_code == 200, r.text
        # Loose check — the SPA shell must at minimum carry its title and the
        # login wrapper class the JS bootstraps against.
        assert SPA_TITLE in r.text
        assert "login-wrap" in r.text or "portal-wrap" in r.text


class TestS9_PatientPortalHealth:
    """The portal exposes its own /api/health that reports DB-backed metrics
    (active sessions, queued appointment requests). It must respond without
    auth — portal users are not Keycloak principals."""

    def test_s9_3_health_reports_service_identity(self, portal_api):
        r = portal_api.get("/health")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"]  == "ok"
        assert body["service"] == SERVICE_NAME
        # Fields the dashboard reads — must always be ints, never null.
        assert isinstance(body.get("active_sessions"), int)
        assert isinstance(body.get("appointment_requests"), int)


class TestS9_PatientPortalAuthSurface:
    """Login validation + fail-closed behaviour on the portal-private routes."""

    def test_s9_4_login_requires_mrn_and_birthdate(self, portal_api):
        r = portal_api.post("/auth/login", json={})
        assert r.status_code == 400

        r = portal_api.post("/auth/login", json={"mrn": "X"})
        assert r.status_code == 400

        r = portal_api.post("/auth/login", json={"birthdate": "1990-01-01"})
        assert r.status_code == 400

    def test_s9_5_login_rejects_unknown_credentials(self, portal_api):
        r = portal_api.post("/auth/login", json={
            "mrn":       "DOES-NOT-EXIST-E2E",
            "birthdate": "1900-01-01",
        })
        # 401 = looked up cleanly and rejected.
        # 503 = OpenMRS unreachable — also acceptable as a fail-closed signal.
        assert r.status_code in (401, 503), r.text

    def test_s9_6_me_without_token_is_401(self, portal_api):
        r = portal_api.get("/me")
        assert r.status_code == 401
        assert r.headers.get("www-authenticate", "").lower().startswith("bearer")

    def test_s9_7_validate_without_token_is_401(self, portal_api):
        r = portal_api.get("/auth/validate")
        assert r.status_code == 401

    def test_s9_8_logout_is_idempotent(self, portal_api):
        # Empty body — must not 500.
        r = portal_api.post("/auth/logout", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        # Unknown token — must not 500 either.
        r = portal_api.post("/auth/logout", json={"token": "not-a-real-session"})
        assert r.status_code == 200


class TestS9_PatientPortalKnownGaps:

    @pytest.mark.xfail(
        reason="Full happy-path session flow (login → /me → logout) requires "
               "an OpenMRS-resident patient with a known MRN + birthdate. "
               "The integration-hub does not seed demo patients into OpenMRS "
               "yet (DEF-006 redirect loop is resolved; OpenMRS write-back "
               "is a separate gap). This test will auto-promote once a "
               "deterministic demo identity is provisioned during stack "
               "bootstrap.",
        strict=False,
    )
    def test_s9_9_full_session_flow(self, portal_api):
        # Demo MRNs are not deterministic across stack rebuilds, so this is
        # gated on a future fixture that creates a real OpenMRS resident.
        login = portal_api.post("/auth/login", json={
            "mrn":       "100GEJ",          # OpenMRS demo Adam Everyman
            "birthdate": "1925-04-08",
        })
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        hdrs = {"Authorization": f"Bearer {token}"}

        me = portal_api.get("/me", headers=hdrs)
        assert me.status_code == 200
        assert me.json().get("mrn") == "100GEJ"

        out = portal_api.post("/auth/logout", headers=hdrs, json={"token": token})
        assert out.status_code == 200
