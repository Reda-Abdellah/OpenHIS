"""
Admin — /api/events/* endpoint tests.

Covers the Redis Streams bridge that backs the live-events widget in the
admin SPA. Redis is not available in unit mode (REDIS_URL is empty), so
these tests only verify routing, schema, and the empty-config path. The
real Redis-backed behaviour is exercised in integration tests.
"""
import pytest


class TestEventsRecent:
    def test_recent_returns_empty_when_redis_unconfigured(self, client, auth_headers):
        """
        GET /api/events/recent returns [] when REDIS_URL is empty.
        This matches the router contract: no-Redis is a graceful path, not a 500.
        """
        resp = client.get("/api/events/recent", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_recent_respects_limit_query_param(self, client, auth_headers):
        """limit accepts values in [1, 500]."""
        resp = client.get("/api/events/recent?limit=10", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_recent_rejects_out_of_range_limit(self, client, auth_headers):
        """limit > 500 is rejected by FastAPI validation (Query le=500)."""
        resp = client.get("/api/events/recent?limit=9999", headers=auth_headers)
        assert resp.status_code == 422


class TestEventsStream:
    def test_stream_endpoint_is_mounted(self, client, auth_headers):
        """
        GET /api/events/stream returns 200 with the text/event-stream media type.
        We don't drain the stream — just confirm the route exists and the
        content-type is correct.
        """
        with client.stream("GET", "/api/events/stream", headers=auth_headers) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
