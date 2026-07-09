"""
Tests for admin service — audit log.

Asserts that write operations (POST, PUT, DELETE) produce audit entries.
"""
import os
import pytest


def test_audit_log_endpoint_returns_list(client, auth_headers):
    """GET /api/audit should return a list (admin v2.0 uses Keycloak-only auth;
    local login no longer produces audit entries).
    """
    resp = client.get("/api/audit", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()
    assert isinstance(entries, list)


def test_audit_log_records_registry_post(client, auth_headers):
    """Registering a service via POST should create an audit entry."""
    client.post("/api/registry", json={
        "name": "audited-svc",
        "profile": "base",
        "internal_url": "http://audited:9000",
        "health_url": "http://audited:9000/health",
    }, headers=auth_headers)

    resp = client.get("/api/audit", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()
    write_actions = [
        e for e in entries
        if any(kw in (e.get("action") or "").lower()
               for kw in ("register", "create", "post"))
    ]
    assert len(write_actions) >= 1, (
        f"Expected audit entries for write operations, got: {[e.get('action') for e in entries]}"
    )


@pytest.mark.skipif(
    os.environ.get("DEV_MODE") == "true",
    reason="DEV_MODE=true bypasses auth enforcement; test belongs in integration suite"
)
def test_audit_requires_auth(client):
    """Audit log endpoint must require authentication."""
    resp = client.get("/api/audit")
    assert resp.status_code in (401, 403)
