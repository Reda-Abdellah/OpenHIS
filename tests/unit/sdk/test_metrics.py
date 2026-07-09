"""
Tests for openhis_sdk.metrics — Prometheus metrics for OpenHIS services.

Covers:
  * MetricsMiddleware: counter + latency histogram, route-template labels,
    "unmatched" for 404s, /metrics self-skip, 500 recording on exceptions
  * GET /metrics endpoint (text exposition) and JWTMiddleware exemption
  * gauge() / register_callback_gauge() helpers
  * openhis_dlq_depth pull-based gauge (XLEN at scrape, fakeredis-backed;
    absent without REDIS_URL; never breaks the scrape on Redis errors)
  * the zero-dependency fallback backend's text exposition format 0.0.4

No Docker, no network.
"""
import re

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import openhis_sdk.auth as sdk_auth
import openhis_sdk.metrics as metrics
from openhis_sdk.metrics import (
    DLQ_STREAMS,
    MetricsMiddleware,
    gauge,
    metrics_router,
    register_callback_gauge,
)

# NB: label values may contain '{'/'}' (route templates like /items/{id}),
# so the labels group is greedy and the regex is anchored on the line end.
_SAMPLE_RE = re.compile(r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})? (?P<value>\S+)$')
_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _samples(text: str, metric: str) -> list[tuple[dict, float]]:
    """Parse exposition text; return [(labels_dict, value)] for `metric`."""
    out = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m or m.group("name") != metric:
            continue
        labels = dict(_LABEL_RE.findall(m.group("labels") or ""))
        out.append((labels, float(m.group("value"))))
    return out


def _make_app(service: str) -> TestClient:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware, service=service)
    app.include_router(metrics_router)

    @app.get("/api/items/{item_id}")
    def get_item(item_id: str) -> dict:
        return {"item_id": item_id}

    @app.get("/api/boom")
    def boom() -> dict:
        raise RuntimeError("kaboom")

    return TestClient(app, raise_server_exceptions=False)


# ── middleware ────────────────────────────────────────────────────────────────


class TestMetricsMiddleware:
    def test_counter_and_histogram_recorded_with_route_template(self):
        client = _make_app("svc-mw-test")
        assert client.get("/api/items/1").status_code == 200
        assert client.get("/api/items/2").status_code == 200

        text = client.get("/metrics").text
        counted = [
            (labels, v)
            for labels, v in _samples(text, "openhis_http_requests_total")
            if labels.get("service") == "svc-mw-test" and labels.get("status") == "200"
        ]
        assert len(counted) == 1
        labels, value = counted[0]
        # route template, never the raw URL → bounded cardinality
        assert labels["path"] == "/api/items/{item_id}"
        assert labels["method"] == "GET"
        assert value == 2.0

        hist_counts = [
            v
            for labels, v in _samples(text, "openhis_http_request_duration_seconds_count")
            if labels.get("service") == "svc-mw-test"
            and labels.get("path") == "/api/items/{item_id}"
        ]
        assert hist_counts == [2.0]

    def test_unrouted_request_labeled_unmatched(self):
        client = _make_app("svc-unmatched-test")
        assert client.get("/no/such/route").status_code == 404

        text = client.get("/metrics").text
        recorded = [
            labels
            for labels, _ in _samples(text, "openhis_http_requests_total")
            if labels.get("service") == "svc-unmatched-test"
        ]
        assert recorded and all(l["path"] == "unmatched" for l in recorded)
        assert recorded[0]["status"] == "404"

    def test_metrics_endpoint_not_self_recorded(self):
        client = _make_app("svc-selfskip-test")
        client.get("/metrics")
        text = client.get("/metrics").text
        assert all(
            labels.get("path") != "/metrics"
            for labels, _ in _samples(text, "openhis_http_requests_total")
            if labels.get("service") == "svc-selfskip-test"
        )

    def test_exception_recorded_as_500_and_reraised(self):
        client = _make_app("svc-boom-test")
        assert client.get("/api/boom").status_code == 500

        text = client.get("/metrics").text
        statuses = [
            labels["status"]
            for labels, _ in _samples(text, "openhis_http_requests_total")
            if labels.get("service") == "svc-boom-test"
        ]
        assert statuses == ["500"]

    def test_exposition_content_type_is_prometheus_text(self):
        client = _make_app("svc-ct-test")
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")


# ── JWT exemption ─────────────────────────────────────────────────────────────


class TestJWTExemption:
    def test_metrics_in_skip_prefixes(self):
        assert "/metrics" in sdk_auth._SKIP_PREFIXES

    def test_metrics_bypasses_jwt_middleware(self, monkeypatch):
        # Force the enforcing branch: DEV_MODE off, Keycloak "configured".
        monkeypatch.setattr(sdk_auth, "DEV_MODE", False)
        monkeypatch.setattr(sdk_auth, "KEYCLOAK_URL", "http://keycloak-metrics-test.invalid")

        app = FastAPI()
        app.add_middleware(sdk_auth.JWTMiddleware)
        app.add_middleware(MetricsMiddleware, service="svc-jwt-test")
        app.include_router(metrics_router)

        @app.get("/api/protected")
        def protected() -> dict:
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/api/protected").status_code == 401  # enforcement is on
        assert client.get("/metrics").status_code == 200        # scrape is exempt


# ── gauge helpers ─────────────────────────────────────────────────────────────


class TestGaugeHelpers:
    def test_settable_gauge_renders(self):
        g = gauge("openhis_test_settable_gauge", "test gauge")
        g.set(5)
        text, _ = metrics.render_metrics()
        samples = _samples(text.decode(), "openhis_test_settable_gauge")
        assert samples == [({}, 5.0)]

    def test_gauge_factory_is_idempotent(self):
        g1 = gauge("openhis_test_idem_gauge", "test gauge")
        g2 = gauge("openhis_test_idem_gauge", "test gauge")
        assert g1 is g2

    def test_callback_gauge_evaluated_at_scrape(self):
        calls = {"n": 0}

        def cb() -> float:
            calls["n"] += 1
            return 42.0

        register_callback_gauge("openhis_test_cb_gauge", "test cb", cb)
        before = calls["n"]
        text, _ = metrics.render_metrics()
        assert calls["n"] == before + 1
        assert (_samples(text.decode(), "openhis_test_cb_gauge")) == [({}, 42.0)]

    def test_callback_gauge_reregistration_replaces(self):
        register_callback_gauge("openhis_test_replace_gauge", "v1", lambda: 1.0)
        register_callback_gauge("openhis_test_replace_gauge", "v2", lambda: 2.0)
        text, _ = metrics.render_metrics()
        assert _samples(text.decode(), "openhis_test_replace_gauge") == [({}, 2.0)]

    def test_failing_callback_never_breaks_the_scrape(self):
        def bad() -> float:
            raise RuntimeError("scrape-time failure")

        register_callback_gauge("openhis_test_bad_gauge", "boom", bad)
        text, _ = metrics.render_metrics()  # must not raise
        assert _samples(text.decode(), "openhis_test_bad_gauge") == []


# ── DLQ depth gauge ───────────────────────────────────────────────────────────


class TestDLQDepthGauge:
    def test_streams_cover_events_dlq(self):
        assert "openhis:events:dlq" in DLQ_STREAMS

    def test_xlen_sampled_at_scrape_time(self, monkeypatch):
        server = fakeredis.FakeServer()
        seed = fakeredis.FakeRedis(server=server)
        for i in range(3):
            seed.xadd("openhis:events:dlq", {"origin_id": str(i)})
        seed.xadd("openhis:events:dlq", {"origin_id": "x"})

        monkeypatch.setenv("REDIS_URL", "redis://dlq-test-fake:6379")
        monkeypatch.setattr(
            metrics, "_open_redis", lambda url: fakeredis.FakeRedis(server=server)
        )

        text, _ = metrics.render_metrics()
        samples = dict(
            (labels["stream"], v)
            for labels, v in _samples(text.decode(), "openhis_dlq_depth")
        )
        assert samples == {"openhis:events:dlq": 4.0}

    def test_absent_when_redis_url_unset(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        assert metrics.dlq_depth_samples() == []
        text, _ = metrics.render_metrics()
        assert _samples(text.decode(), "openhis_dlq_depth") == []

    def test_redis_failure_never_breaks_the_scrape(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://dlq-down-fake:6379")

        def explode(url: str):
            raise ConnectionError("redis is down")

        monkeypatch.setattr(metrics, "_open_redis", explode)
        assert metrics.dlq_depth_samples() == []
        text, _ = metrics.render_metrics()  # must not raise
        assert _samples(text.decode(), "openhis_dlq_depth") == []


# ── zero-dependency fallback backend ──────────────────────────────────────────


class TestFallbackExposition:
    """The fallback renderer must emit valid text exposition format 0.0.4
    regardless of whether prometheus_client is installed."""

    def test_counter_text_format(self):
        reg = metrics._FallbackRegistry()
        c = reg.counter("demo_total", "A demo counter", ("svc",))
        c.labels(svc="a").inc()
        c.labels(svc="a").inc(2)
        text = reg.render()
        assert "# HELP demo_total A demo counter\n" in text
        assert "# TYPE demo_total counter\n" in text
        assert 'demo_total{svc="a"} 3.0\n' in text

    def test_counter_rejects_negative_increment(self):
        reg = metrics._FallbackRegistry()
        c = reg.counter("neg_total", "nope")
        with pytest.raises(ValueError):
            c.inc(-1)

    def test_gauge_set_and_label_escaping(self):
        reg = metrics._FallbackRegistry()
        g = reg.gauge("demo_gauge", "A demo gauge", ("path",))
        g.labels(path='with"quote\\and\nnewline').set(1.5)
        text = reg.render()
        assert 'demo_gauge{path="with\\"quote\\\\and\\nnewline"} 1.5\n' in text

    def test_histogram_cumulative_buckets_sum_count(self):
        reg = metrics._FallbackRegistry()
        h = reg.histogram("demo_seconds", "A demo histogram", ("svc",),
                          buckets=(0.1, 1.0, 5.0))
        h.labels(svc="a").observe(0.05)   # ≤ all bounds
        h.labels(svc="a").observe(0.5)    # ≤ 1.0 and 5.0
        h.labels(svc="a").observe(7.0)    # > all bounds → only +Inf
        text = reg.render()
        assert "# TYPE demo_seconds histogram\n" in text
        assert 'demo_seconds_bucket{svc="a",le="0.1"} 1.0\n' in text
        assert 'demo_seconds_bucket{svc="a",le="1.0"} 2.0\n' in text
        assert 'demo_seconds_bucket{svc="a",le="5.0"} 2.0\n' in text
        assert 'demo_seconds_bucket{svc="a",le="+Inf"} 3.0\n' in text
        assert 'demo_seconds_sum{svc="a"} 7.55\n' in text
        assert 'demo_seconds_count{svc="a"} 3.0\n' in text

    def test_registry_renders_callback_gauges(self):
        reg = metrics._FallbackRegistry()
        reg.callbacks.append(
            metrics._CallbackGauge(
                "demo_cb", "scrape-time", ("stream",),
                lambda: [(("s1",), 4.0)],
            )
        )
        text = reg.render()
        assert "# TYPE demo_cb gauge\n" in text
        assert 'demo_cb{stream="s1"} 4.0\n' in text

    def test_labels_validation(self):
        reg = metrics._FallbackRegistry()
        c = reg.counter("lbl_total", "labels", ("a", "b"))
        with pytest.raises(ValueError):
            c.labels(a="only-one")
        with pytest.raises(ValueError):
            c.labels("x", b="mixed")
