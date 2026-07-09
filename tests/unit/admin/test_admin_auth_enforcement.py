"""
T-03 — real-auth (DEV_MODE=false) enforcement per admin router.

Boots the admin app via the tests/auth harness (RS256 tokens validated
against an in-memory JWKS — no Keycloak, no network) and asserts per router:

  * 401 with no token,
  * 403 with a valid token lacking the admin role (role-gated routers:
    events, platform, identity),
  * 200/201 with the right token,
  * require_token-only routers (registry, profiles, config, announcements,
    audit) accept any valid platform JWT — intentional, OPM's
    client-credentials token carries no admin realm role.

Public paths (/, /api/health, /api/auth/config) must stay open.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# tests/auth is only auto-inserted on sys.path while its own suite runs —
# add it explicitly so this module can import the harness from tests/unit.
_TESTS_AUTH = str(Path(__file__).resolve().parents[2] / "auth")
if _TESTS_AUTH not in sys.path:
    sys.path.insert(0, _TESTS_AUTH)

import harness  # noqa: E402


@pytest.fixture
def admin_real_auth(tmp_path):
    """(app, client) for the admin service with real JWT validation active."""
    env = {
        "DB_PATH": f"{tmp_path}/admin-auth.db",
        "ROOT_PATH": "",
        "LOG_FORMAT": "text",
        "REDIS_URL": "",
    }
    with harness.isolated_service("admin", env=env, init_db=True) as app:
        yield app, TestClient(app, raise_server_exceptions=False)


# ── Role-gated routers (require_roles("admin")) ──────────────────────────────

ROLE_GATED_GETS = [
    "/api/events/recent",
    "/api/events/stream",
    "/api/platform/topology",
    "/api/platform/profiles",
    "/api/platform/ram",
]


def test_events_and_platform_require_admin_role(admin_real_auth):
    _, client = admin_real_auth
    for path in ROLE_GATED_GETS:
        assert client.get(path).status_code == 401, f"{path}: expected 401 w/o token"
        denied = client.get(path, headers=harness.auth_header(["clinician", "nurse"]))
        assert denied.status_code == 403, (
            f"{path}: expected 403 without admin role, got {denied.status_code}"
        )
        granted = client.get(path, headers=harness.auth_header(["admin"]))
        assert granted.status_code == 200, (
            f"{path}: expected 200 with admin role, got {granted.status_code}"
        )


def test_identity_mutations_require_admin_role(admin_real_auth):
    _, client = admin_real_auth
    cases = [
        ("post", "/api/identity/users", {
            "username": "u", "email": "u@example.org", "first_name": "U",
            "last_name": "V", "roles": ["clinician"], "temporary_password": "x",
        }),
        ("patch", "/api/identity/users/kc-1/roles", {"roles": []}),
        ("delete", "/api/identity/users/kc-1", None),
    ]
    for method, path, body in cases:
        kwargs = {} if body is None else {"json": body}
        assert getattr(client, method)(path, **kwargs).status_code == 401
        denied = getattr(client, method)(
            path, headers=harness.auth_header(["clinician"]), **kwargs
        )
        assert denied.status_code == 403, (
            f"{method.upper()} {path}: expected 403 without admin role, "
            f"got {denied.status_code}"
        )
    # With the admin role the auth layers clear; Keycloak (a .invalid host)
    # is unreachable so the handler itself answers 503 — that is the proof
    # the 401/403 layers passed, without needing a live IdP.
    resp = client.delete("/api/identity/users/kc-1",
                         headers=harness.auth_header(["admin"]))
    assert resp.status_code == 503


# ── require_token-only routers ───────────────────────────────────────────────

def test_registry_mutations_require_token(admin_real_auth):
    _, client = admin_real_auth
    payload = {"name": "auth-probe", "profile": "base",
               "internal_url": "http://p:1", "health_url": "http://p:1/h"}

    assert client.post("/api/registry", json=payload).status_code == 401
    assert client.delete("/api/registry/auth-probe").status_code == 401

    # Any valid platform JWT is enough (OPM service tokens have no realm role).
    ok = client.post("/api/registry", json=payload,
                     headers=harness.auth_header([]))
    assert ok.status_code == 201
    gone = client.delete("/api/registry/auth-probe",
                         headers=harness.auth_header([]))
    assert gone.status_code == 200


def test_config_audit_announcements_require_token(admin_real_auth):
    _, client = admin_real_auth

    assert client.get("/api/audit").status_code == 401
    assert client.put("/api/config/maintenance_mode",
                      json={"value": "true"}).status_code == 401
    assert client.post("/api/announcements",
                       json={"title": "t", "body": "b"}).status_code == 401

    headers = harness.auth_header(["admin"])
    assert client.get("/api/audit", headers=headers).status_code == 200
    assert client.put("/api/config/maintenance_mode",
                      json={"value": "false"}, headers=headers).status_code == 200
    assert client.post("/api/announcements",
                       json={"title": "t", "body": "b"},
                       headers=headers).status_code == 201


def test_profiles_mutations_require_token(admin_real_auth, monkeypatch, tmp_path):
    app, client = admin_real_auth

    assert client.get("/api/profiles/active").status_code == 401
    assert client.post("/api/profiles/enable",
                       json={"profiles": ["emr"]}).status_code == 401
    assert client.post("/api/profiles/disable",
                       json={"profiles": ["emr"]}).status_code == 401

    # Redirect the .env write + nginx regen before the authorized call so the
    # test never touches the real repo .env.
    profiles_mod = sys.modules["routers.profiles"]
    env_file = str(tmp_path / "profiles.env")
    with open(env_file, "w") as f:
        f.write("OPENHIS_PROFILES=\n")
    monkeypatch.setattr(profiles_mod, "_ENV_FILE", env_file)
    monkeypatch.setattr(profiles_mod, "_regen_nginx", lambda active: None)

    ok = client.post("/api/profiles/enable", json={"profiles": ["laboratory"]},
                     headers=harness.auth_header(["admin"]))
    assert ok.status_code == 200

    # GET /active is readable with any valid platform JWT (no role needed).
    active = client.get("/api/profiles/active",
                        headers=harness.auth_header([]))
    assert active.status_code == 200
    assert "laboratory" in active.json()["profiles"]


# ── Public surface stays open ────────────────────────────────────────────────

def test_public_paths_need_no_token(admin_real_auth):
    _, client = admin_real_auth
    for path in ("/", "/api/health", "/api/auth/config", "/docs"):
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"{path}: public route must stay open, got {resp.status_code}"
        )


def test_foreign_and_expired_tokens_rejected(admin_real_auth):
    _, client = admin_real_auth
    foreign = harness.make_foreign_token(["admin"])
    expired = harness.make_token(["admin"], expires_in_s=-300)
    for token in (foreign, expired):
        resp = client.get("/api/events/recent",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
