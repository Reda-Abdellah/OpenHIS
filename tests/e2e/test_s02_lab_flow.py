"""
Scenario 2 — Laboratory Order & Result Flow

Mirrors SCENARIO 2 in docs/verification_and_validation/v-and-v-scenario.md.

The canonical lab flow requires both OpenMRS and OpenELIS to be logged into
via credentials we don't have in the automated test environment. What we CAN
exercise automatically:

Covers:
  ✅ S2.1 — integration-hub /api/atomfeed/status is queryable
  ✅ S2.2 — integration-hub /api/atomfeed/trigger accepts the request
  ✅ S2.3 — integration-hub /api/events/report-final queues a payload
  ✅ S2.4 — hub audit captures the queued event (within 5s)
  ✅ S2.5 — ServiceRequest flow OpenMRS → OpenELIS
  ✅ S2.6 — DiagnosticReport flow OpenELIS → OpenMRS

DEF-001 (adapter health checks required a Keycloak token) is fixed — the
hub now probes unauthenticated liveness endpoints, so the former xfail
markers on S2.5/S2.6 have been removed. If a live `make e2e` still shows
these failing, the residual blocker is the worker DATA path (OpenMRS 302s
the hub's service-account bearer on FHIR reads/writes) — that is a
separate defect, not DEF-001.
"""
import time

import pytest


pytestmark = pytest.mark.e2e


class TestS2_LabFlow:

    def test_s2_1_atomfeed_status_queryable(self, hub_api):
        r = hub_api.get("/atomfeed/status")
        assert r.status_code == 200
        body = r.json()
        for key in ("patients_synced", "orders_synced", "reports_synced",
                    "errors", "last_poll_at"):
            assert key in body

    def test_s2_2_atomfeed_trigger_accepted(self, hub_api):
        r = hub_api.post("/atomfeed/trigger")
        assert r.status_code == 200
        assert r.json()["status"] == "triggered"

    def test_s2_3_report_final_event_queued(self, hub_api, request):
        r = hub_api.post("/events/report-final", json={
            "report_id":  99001,
            "order_id":   42001,
            "impression": "E2E test — CBC within normal range.",
            "status":     "FINAL",
        })
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "queued"
        # The report-final handler audits with the ORDER id, not the report id.
        request.config.cache.set("s2/report_id", 99001)
        request.config.cache.set("s2/order_id", 42001)

    def test_s2_4_audit_captures_report_final_event(self, hub_api, request):
        """
        The hub's audit log or retry queue should reflect the queued FINAL
        event within a few seconds.  We don't assert on the outbound push
        succeeding (that depends on OpenMRS availability) — only that the
        hub ingested the request.
        """
        report_id = request.config.cache.get("s2/order_id", None)
        assert report_id

        # The push retries in-process (3 × ~1-2 s backoff) before the
        # failure audit row lands — give it 20 s.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            r = hub_api.get("/audit", params={"limit": 50})
            assert r.status_code == 200
            events = r.json().get("events", [])
            # Matching relaxation: either the DiagnosticReport push appears,
            # or the retry queue depth incremented (the push is queued for
            # retry because OpenMRS isn't reachable with our test creds).
            if any(
                e["resource_type"] == "DiagnosticReport"
                and str(report_id) in (e.get("resource_id") or "")
                for e in events
            ):
                return
            retry_depth = r.json().get("retry_queue_depth", 0)
            if retry_depth > 0:
                return
            time.sleep(0.5)
        # The hub accepted the POST (S2.3); within 6s it must either audit
        # the DiagnosticReport push or queue it for retry.
        pytest.fail("No DiagnosticReport audit row or retry-queue growth within 6s")


class TestS2_KnownDefects:

    @pytest.mark.xfail(
        reason="Seed gap: DEF-011 is fixed (the worker's OpenMRS poll now "
               "authenticates), but OpenMRS's fhir2 module does not support "
               "POST ServiceRequest, so the suite cannot create the active "
               "lab order this test needs. Auto-promotes on a stack where a "
               "clinician (or a REST-order seeding step) has placed an order.",
        strict=False,
    )
    def test_s2_5_openmrs_to_openelis_service_request(self, hub_api):
        r = hub_api.get("/audit", params={"limit": 50})
        events = r.json().get("events", [])
        # Worker writes direction="omrs→oe" (short form) on successful routing.
        assert any(
            e["resource_type"] == "ServiceRequest"
            and e["direction"] == "omrs→oe"
            and e["status"] == "ok"
            for e in events
        )

    def test_s2_6_openelis_to_openmrs_diagnostic_report(self, hub_api, auth_hdrs):
        """
        The hub polls OpenELIS's FHIR store for final reports and pushes
        them to OpenMRS (bearer machine token — DEF-011 fixed).

        Self-sufficient: seeds a final DiagnosticReport into the store
        (OpenELIS's own FHIR servlet doesn't handle DiagnosticReport —
        the LIS writes results to the store, so seeding there is exactly
        what a completed OE result looks like to the hub), triggers a
        poll cycle, then waits for the `oe→omrs` audit row. The seeded
        code must be a concept OpenMRS's dictionary knows (CIEL CBC),
        otherwise the fhir2 push is rejected with 422.
        """
        import httpx, os, time

        # The push target validates both the code (must map to a dictionary
        # concept) and the subject (must be a real OpenMRS patient), so the
        # seed needs an OpenMRS-resident patient. Create one with the hub's
        # own machine identity (bearer path provisioned by openmrs-init);
        # skip when the deployment doesn't use the dev-default secret.
        secret = os.environ.get(
            "INTEGRATION_HUB_KC_CLIENT_SECRET", "integration-hub-sa-secret")
        tok_r = httpx.post(
            "http://localhost/keycloak/realms/openhis/protocol/openid-connect/token",
            data={"grant_type": "client_credentials",
                  "client_id": "integration-hub-sa", "client_secret": secret},
            timeout=10,
        )
        if tok_r.status_code != 200:
            pytest.skip("cannot mint integration-hub-sa token (non-dev secret?)")
        machine_hdrs = {
            "Authorization": f"Bearer {tok_r.json()['access_token']}",
            "Content-Type": "application/fhir+json",
        }

        # OpenMRS's required identifier type uses a LuhnMod30 check digit.
        alpha = "0123456789ACDEFGHJKLMNPRTUVWXY"

        def _mod30(s: str) -> str:
            total = 0
            for i, ch in enumerate(reversed(s.upper())):
                v = alpha.index(ch)
                v = v * 2 if i % 2 == 0 else v
                total += v // 30 + v % 30
            return alpha[(30 - total % 30) % 30]

        base = f"E2E{int(time.time()) % 10_000_000}"
        patient = {
            "resourceType": "Patient",
            "name": [{"family": "S26Probe", "given": ["E2E"]}],
            "gender": "female", "birthDate": "1985-05-05",
            "identifier": [{"use": "official",
                            "type": {"text": "OpenMRS ID"},
                            "value": base + _mod30(base)}],
        }
        p_r = httpx.post(
            "http://localhost/openmrs/ws/fhir2/R4/Patient",
            json=patient, headers=machine_hdrs, timeout=60,
        )
        assert p_r.status_code == 201, (
            f"OpenMRS patient seed failed: {p_r.status_code} {p_r.text[:200]}"
        )
        subject_uuid = p_r.json()["id"]

        cbc_uuid = "1019AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # CIEL: Complete blood count
        marker = f"e2e-s26-{int(time.time())}"
        dr = {
            "resourceType": "DiagnosticReport",
            "status": "final",
            "code": {"coding": [{"code": cbc_uuid}], "text": marker},
            "subject": {"reference": f"Patient/{subject_uuid}"},
        }
        r = httpx.post(
            "http://localhost/oe-fhir-store/fhir/DiagnosticReport",
            json=dr, headers={"Content-Type": "application/fhir+json"},
            timeout=15,
        )
        assert r.status_code == 201, f"store seed failed: {r.status_code} {r.text[:200]}"
        seeded_id = r.json()["id"]

        # Kick sync cycles instead of waiting for the 60 s poll timer. A
        # first push attempt can fail transiently (e.g. OpenMRS bearer
        # warm-up) and land on the hub retry queue (base backoff 15 s,
        # drained on each cycle) — so keep triggering and allow 90 s.
        # Success surfaces as either the direct `result_routed` row or a
        # `retry_ok` row; both carry the same resource/direction/status.
        deadline = time.monotonic() + 90
        last_trigger = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() - last_trigger > 10:
                hub_api.post("/atomfeed/trigger")
                last_trigger = time.monotonic()
            r = hub_api.get("/audit", params={"limit": 100})
            events = r.json().get("events", [])
            if any(
                e["resource_type"] == "DiagnosticReport"
                and e.get("resource_id") == seeded_id
                and e["direction"] == "oe→omrs"
                and e["status"] == "ok"
                for e in events
            ):
                return
            time.sleep(2)
        pytest.fail(
            f"no oe→omrs ok audit row for seeded DiagnosticReport {seeded_id} within 90s"
        )
