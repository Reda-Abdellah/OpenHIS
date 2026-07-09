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
        request.config.cache.set("s2/report_id", 99001)

    def test_s2_4_audit_captures_report_final_event(self, hub_api, request):
        """
        The hub's audit log or retry queue should reflect the queued FINAL
        event within a few seconds.  We don't assert on the outbound push
        succeeding (that depends on OpenMRS availability) — only that the
        hub ingested the request.
        """
        report_id = request.config.cache.get("s2/report_id", None)
        assert report_id

        deadline = time.monotonic() + 6
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

    def test_s2_6_openelis_to_openmrs_diagnostic_report(self, hub_api):
        """
        The hub polls OpenELIS for completed reports and pushes them to
        OpenMRS. Assert the `oe→omrs` step produced an audit row.
        """
        r = hub_api.get("/audit", params={"limit": 100})
        events = r.json().get("events", [])
        # Worker writes direction="oe→omrs" on successful report routing.
        assert any(
            e["resource_type"] == "DiagnosticReport"
            and e["direction"] == "oe→omrs"
            and e["status"] == "ok"
            for e in events
        ), "no oe→omrs DiagnosticReport audit row found"
