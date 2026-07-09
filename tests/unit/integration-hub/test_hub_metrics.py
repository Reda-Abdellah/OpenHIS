"""Integration Hub — openhis_sdk.metrics wiring smoke test (representative service)."""
import respx


class TestHubMetricsWiring:
    def test_metrics_endpoint_exposes_request_counter(self, client):
        # Drive one real request through the middleware (upstream probes are
        # mocked/unreachable — degraded health still returns 200), then scrape.
        with respx.mock:
            assert client.get("/api/health").status_code == 200

        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "openhis_http_requests_total" in r.text
        assert 'service="integration-hub"' in r.text
        assert 'path="/api/health"' in r.text

    def test_metrics_is_public_no_token_needed(self, client):
        # /metrics sits in openhis_sdk.auth._SKIP_PREFIXES — even with the
        # hub's JWTMiddleware active, the scrape needs no Authorization.
        r = client.get("/metrics")
        assert r.status_code == 200
