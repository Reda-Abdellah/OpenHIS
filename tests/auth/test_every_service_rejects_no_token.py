"""
Deny-by-default auth enforcement, per service (T-04, F#2/F#33).

For every native service app that can be imported in-process, boot it with
DEV_MODE=false and KEYCLOAK_URL set to a dummy host (real JWT validation
active, JWKS mocked in-memory) and assert:

  * a protected route returns 401 with no / garbage / foreign-signed /
    expired bearer tokens,
  * a require_roles route returns 403 when the token lacks the role,
  * the same routes return 200 (or at least clear the auth layers) with a
    valid harness-minted token,
  * known-public routes (/api/health, /docs, /api/auth/config) stay open.

The per-service probe paths live in ``harness.SERVICES`` — extend that
catalog (e.g. with new RoleGate entries) as T-02/T-03/T-05/T-06 add role
checks, so regressions are caught here automatically.
"""
import pytest
from fastapi.testclient import TestClient

import harness


def _request(client: TestClient, method: str, path: str, **kwargs):
    return getattr(client, method)(path, **kwargs)


def test_protected_route_rejects_missing_token(service):
    spec, client = service
    resp = _request(client, spec.method, spec.protected_path)
    assert resp.status_code == 401, (
        f"{spec.name}: {spec.method.upper()} {spec.protected_path} without a "
        f"token must return 401, got {resp.status_code}"
    )


def test_protected_route_rejects_garbage_token(service):
    spec, client = service
    resp = _request(
        client, spec.method, spec.protected_path,
        headers={"Authorization": "Bearer not.a.jwt"},
    )
    assert resp.status_code == 401, (
        f"{spec.name}: garbage bearer token must return 401, got {resp.status_code}"
    )


def test_protected_route_rejects_foreign_signature(service):
    spec, client = service
    token = harness.make_foreign_token(spec.granted_roles)
    resp = _request(
        client, spec.method, spec.protected_path,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401, (
        f"{spec.name}: token signed by a key outside the JWKS must return "
        f"401, got {resp.status_code}"
    )


def test_protected_route_rejects_expired_token(service):
    spec, client = service
    token = harness.make_token(spec.granted_roles, expires_in_s=-300)
    resp = _request(
        client, spec.method, spec.protected_path,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401, (
        f"{spec.name}: expired token must return 401, got {resp.status_code}"
    )


def test_role_gated_route_rejects_missing_role(service):
    spec, client = service
    if spec.role_gate is None:
        pytest.skip(
            f"{spec.name}: no role-gated route yet (middleware-only); add a "
            "RoleGate to harness.SERVICES when T-02/T-03/T-05/T-06 land"
        )
    gate = spec.role_gate
    resp = _request(
        client, gate.method, gate.path,
        headers=harness.auth_header(gate.denied_roles),
    )
    assert resp.status_code == 403, (
        f"{spec.name}: {gate.method.upper()} {gate.path} with roles "
        f"{gate.denied_roles} must return 403, got {resp.status_code}"
    )


def test_protected_route_accepts_valid_token(service):
    spec, client = service
    if spec.authorized_statuses == ():
        pytest.skip(
            f"{spec.name}: protected route sits behind a service-local "
            "session layer that rejects platform JWTs by design"
        )
    resp = _request(
        client, spec.method, spec.protected_path,
        headers=harness.auth_header(spec.granted_roles),
    )
    if spec.authorized_statuses is None:
        # Handler needs infra (e.g. MPI's PostgreSQL) unavailable in this
        # run; the point is that middleware + role deps let the call through.
        assert resp.status_code not in (401, 403), (
            f"{spec.name}: valid token with roles {spec.granted_roles} was "
            f"rejected with {resp.status_code}"
        )
    else:
        assert resp.status_code in spec.authorized_statuses, (
            f"{spec.name}: valid token with roles {spec.granted_roles} "
            f"expected {spec.authorized_statuses}, got {resp.status_code}"
        )


def test_public_routes_need_no_token(service):
    spec, client = service
    for path in spec.public_paths:
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"{spec.name}: public route {path} must return 200 without a "
            f"token, got {resp.status_code}"
        )
