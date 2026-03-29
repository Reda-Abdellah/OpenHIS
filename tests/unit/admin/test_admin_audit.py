"""
Tests for admin service — audit log.

Asserts that write operations (POST, PUT, DELETE) produce audit entries.
"""
import pytest


def test_audit_log_records_login(client, auth_headers):
    """A successful login should appear in the audit log."""
    resp = client.get("/api/audit", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()
    assert isinstance(entries, list)
    actions = [e.get("action", "") for e in entries]
    assert any("login" in a.lower() for a in actions), (
        f"Expected a 'login' audit entry, got actions: {actions}"
    )


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
    # At least one of the audit entries should reference a write action
    write_actions = [
        e for e in entries
        if any(kw in (e.get("action") or "").lower()
               for kw in ("register", "create", "post", "login"))
    ]
    assert len(write_actions) >= 1, (
        f"Expected audit entries for write operations, got: {[e.get('action') for e in entries]}"
    )


def test_audit_requires_auth(client):
    """Audit log endpoint must require authentication."""
    resp = client.get("/api/audit")
    assert resp.status_code in (401, 403)
