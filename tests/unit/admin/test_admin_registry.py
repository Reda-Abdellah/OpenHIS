"""
Tests for admin service — service registry endpoints.

Covers: register, deregister, health probe (GET /api/registry).
"""
import pytest


def test_registry_list_empty_after_seed(client, auth_headers):
    """Registry should have at least the base services after startup seed."""
    resp = client.get("/api/registry", headers=auth_headers)
    assert resp.status_code == 200
    services = resp.json()
    assert isinstance(services, list)
    names = {s["name"] for s in services}
    # Base services seeded on startup
    assert "admin" in names
    assert "mpi" in names


def test_registry_register_service(client, auth_headers):
    """POST /api/registry should add a new service entry."""
    payload = {
        "name": "test-service",
        "profile": "test",
        "internal_url": "http://test-service:9999",
        "health_url": "http://test-service:9999/api/health",
        "nginx_path": "/test-service",
    }
    resp = client.post("/api/registry", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test-service"

    # Verify it appears in the list
    resp2 = client.get("/api/registry", headers=auth_headers)
    names = {s["name"] for s in resp2.json()}
    assert "test-service" in names


def test_registry_deregister_service(client, auth_headers):
    """DELETE /api/registry/{name} should remove the entry."""
    # First register
    client.post("/api/registry", json={
        "name": "svc-to-delete",
        "profile": "base",
        "internal_url": "http://x:1234",
        "health_url": "http://x:1234/health",
    }, headers=auth_headers)

    # Then deregister
    resp = client.delete("/api/registry/svc-to-delete", headers=auth_headers)
    assert resp.status_code == 200

    # Verify gone
    resp2 = client.get("/api/registry", headers=auth_headers)
    names = {s["name"] for s in resp2.json()}
    assert "svc-to-delete" not in names


def test_registry_requires_auth(client):
    """Registry endpoints should require authentication."""
    resp = client.get("/api/registry")
    assert resp.status_code in (401, 403)

    resp = client.post("/api/registry", json={
        "name": "x", "profile": "base",
        "internal_url": "http://x:1", "health_url": "http://x:1/h",
    })
    assert resp.status_code in (401, 403)
