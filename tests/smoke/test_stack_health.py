"""
Smoke tests — require a running Docker Compose stack.
Run after `make up` or on merge to main via CI.

These tests check that all core services pass their health endpoints.
"""
import pytest
import httpx

BASE = "http://localhost"

CORE_HEALTH_ENDPOINTS = [
    ("/mpi/health", "mpi"),
    ("/hub/health", "integration-hub"),
    ("/hl7/health", "hl7"),
    ("/admin/health", "admin"),
]


@pytest.mark.smoke
@pytest.mark.parametrize("path,service", CORE_HEALTH_ENDPOINTS)
def test_service_is_healthy(path: str, service: str) -> None:
    resp = httpx.get(f"{BASE}{path}", timeout=10)
    assert resp.status_code == 200, f"{service} health check failed: {resp.status_code}"
