"""Admin — openhis_sdk.metrics wiring smoke test (representative service)."""


class TestAdminMetricsWiring:
    def test_metrics_endpoint_exposes_request_counter(self, client):
        # Drive one real request through the middleware, then scrape.
        assert client.get("/api/health").status_code == 200

        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "openhis_http_requests_total" in r.text
        # The middleware was wired with service="admin" and recorded the
        # route template of the request above.
        assert 'service="admin"' in r.text
        assert 'path="/api/health"' in r.text

    def test_metrics_does_not_record_itself(self, client):
        client.get("/metrics")
        r = client.get("/metrics")
        assert 'path="/metrics"' not in r.text
