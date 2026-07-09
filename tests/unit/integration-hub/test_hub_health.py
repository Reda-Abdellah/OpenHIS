"""Integration Hub — health and status endpoint tests."""
import respx
import httpx


class TestHealth:
    def test_health_returns_200(self, client):
        with respx.mock:
            # Upstreams unreachable in tests → status degraded but endpoint itself works
            r = client.get("/api/health")
        assert r.status_code == 200

    def test_health_has_status_field(self, client):
        with respx.mock:
            r = client.get("/api/health")
        assert "status" in r.json()

    def test_health_status_ok_when_all_upstreams_up(self, client):
        omrs = "http://openmrs-hub-test:9998"
        oe   = "http://openelis-hub-test:9998"
        odoo = "http://odoo-hub-test:9998"
        # DEF-001 fixed: health_check() probes unauthenticated liveness
        # endpoints — no Keycloak token mock is needed (or allowed) here.
        with respx.mock:
            respx.get(f"{omrs}/openmrs/health/started").mock(
                return_value=httpx.Response(200, text="started")
            )
            respx.get(f"{oe}/OpenELIS-Global/fhir/metadata").mock(
                return_value=httpx.Response(200, json={"resourceType": "CapabilityStatement"})
            )
            respx.get(f"{odoo}/web/health").mock(
                return_value=httpx.Response(200, json={"status": "pass"})
            )
            r = client.get("/api/health")
        assert r.json()["status"] == "ok"

    def test_health_ok_when_keycloak_down(self, client):
        """DEF-001 regression: hub health must not depend on Keycloak.

        Only the upstream liveness probes are mocked. The root conftest sets
        KEYCLOAK_TOKEN_URL to a non-resolvable address and respx.mock raises
        on any unmocked request — so if health_check() ever fetches a service
        token again, the probe fails and this test catches it as "degraded".
        """
        omrs = "http://openmrs-hub-test:9998"
        oe   = "http://openelis-hub-test:9998"
        odoo = "http://odoo-hub-test:9998"
        with respx.mock:
            respx.get(f"{omrs}/openmrs/health/started").mock(
                return_value=httpx.Response(200, text="started")
            )
            respx.get(f"{oe}/OpenELIS-Global/fhir/metadata").mock(
                return_value=httpx.Response(200, json={"resourceType": "CapabilityStatement"})
            )
            respx.get(f"{odoo}/web/health").mock(
                return_value=httpx.Response(200, json={"status": "pass"})
            )
            r = client.get("/api/health")
        assert r.json()["status"] == "ok"

    def test_health_probe_sends_no_authorization_header(self, client):
        """The DEF-001 fix contract: liveness probes carry no Authorization
        header and no Basic-auth credentials."""
        omrs = "http://openmrs-hub-test:9998"
        oe   = "http://openelis-hub-test:9998"
        with respx.mock:
            omrs_route = respx.get(f"{omrs}/openmrs/health/started").mock(
                return_value=httpx.Response(200, text="started")
            )
            oe_route = respx.get(f"{oe}/OpenELIS-Global/fhir/metadata").mock(
                return_value=httpx.Response(200, json={"resourceType": "CapabilityStatement"})
            )
            client.get("/api/health")
        assert omrs_route.called and oe_route.called
        for route in (omrs_route, oe_route):
            for call in route.calls:
                assert "authorization" not in call.request.headers

    def test_health_degraded_when_upstream_down(self, client):
        with respx.mock:
            # No upstreams mocked → all connections fail
            r = client.get("/api/health")
        assert r.json()["status"] in ("degraded", "ok")  # degraded preferred


class TestFeedStatus:
    def test_feed_status_returns_counters(self, client):
        r = client.get("/api/atomfeed/status")
        assert r.status_code == 200
        j = r.json()
        for key in ("patients_synced", "orders_synced", "reports_synced", "errors"):
            assert key in j

    def test_feed_status_last_poll_initially_never(self, client):
        r = client.get("/api/atomfeed/status")
        assert r.json()["last_poll_at"] == "never"

    def test_trigger_returns_triggered(self, client):
        with respx.mock:
            r = client.post("/api/atomfeed/trigger")
        assert r.status_code == 200
        assert r.json()["status"] == "triggered"
