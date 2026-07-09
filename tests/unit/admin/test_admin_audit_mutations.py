"""
DEF-002 — every mutating admin endpoint must write an audit_log row.

Parametrized sweep over all admin mutations (registry, identity, config,
profiles, announcements): perform the mutation, then assert GET /api/audit
contains a row with the expected action and a non-empty actor.

Side effects are neutralized:
  * keycloak_client / provisioning are stubbed (identity routes),
  * routers.profiles._ENV_FILE is redirected to a tmp file and
    _regen_nginx is a no-op (profiles routes).
"""
import sys
from unittest.mock import AsyncMock

import pytest


# ── Side-effect guards ───────────────────────────────────────────────────────

@pytest.fixture
def mutation_guards(client, monkeypatch, tmp_path):
    """Stub external side effects so every mutation can run in unit mode."""
    import keycloak_client
    import provisioning

    monkeypatch.setattr(keycloak_client, "create_user",
                        AsyncMock(return_value="kc-uuid-audit"))
    monkeypatch.setattr(keycloak_client, "assign_roles", AsyncMock(return_value=None))
    monkeypatch.setattr(keycloak_client, "set_roles",    AsyncMock(return_value=None))
    monkeypatch.setattr(keycloak_client, "disable_user", AsyncMock(return_value=None))
    monkeypatch.setattr(provisioning, "provision_user",   AsyncMock(return_value={}))
    monkeypatch.setattr(provisioning, "deprovision_user", AsyncMock(return_value=None))

    profiles_mod = sys.modules["routers.profiles"]
    env_file = str(tmp_path / "profiles.env")
    with open(env_file, "w") as f:
        f.write("OPENHIS_PROFILES=emr\n")
    monkeypatch.setattr(profiles_mod, "_ENV_FILE", env_file)
    monkeypatch.setattr(profiles_mod, "_regen_nginx", lambda active: None)


def _register_probe_service(client, headers):
    client.post("/api/registry", json={
        "name": "audit-probe-svc",
        "profile": "base",
        "internal_url": "http://probe:9000",
        "health_url": "http://probe:9000/health",
    }, headers=headers)


def _create_announcement(client, headers):
    r = client.post("/api/announcements",
                    json={"title": "probe", "body": "probe body"},
                    headers=headers)
    assert r.status_code == 201


MUTATIONS = [
    pytest.param("post", "/api/registry",
                 {"name": "audited-new-svc", "profile": "base",
                  "internal_url": "http://a:1", "health_url": "http://a:1/h"},
                 None, "service-registered", id="registry-register"),
    pytest.param("delete", "/api/registry/audit-probe-svc",
                 None, _register_probe_service,
                 "service-deregistered", id="registry-deregister"),
    pytest.param("post", "/api/identity/users",
                 {"username": "audrey", "email": "audrey@example.org",
                  "first_name": "Audrey", "last_name": "Tautou",
                  "roles": ["clinician"], "temporary_password": "x"},
                 None, "user-created", id="identity-create"),
    pytest.param("patch", "/api/identity/users/kc-uuid-1/roles",
                 {"roles": ["radiologist"]},
                 None, "user-roles-updated", id="identity-roles"),
    pytest.param("delete", "/api/identity/users/kc-uuid-1",
                 None, None, "user-disabled", id="identity-disable"),
    pytest.param("post", "/api/profiles/enable",
                 {"profiles": ["laboratory"]},
                 None, "profiles-enabled", id="profiles-enable"),
    pytest.param("post", "/api/profiles/disable",
                 {"profiles": ["emr"]},
                 None, "profiles-disabled", id="profiles-disable"),
    pytest.param("put", "/api/config/maintenance_mode",
                 {"value": "true"},
                 None, "config-changed", id="config-set"),
    pytest.param("post", "/api/announcements",
                 {"title": "Maintenance", "body": "Sunday 02:00"},
                 None, "announcement-created", id="announcement-create"),
    pytest.param("delete", "/api/announcements/1",
                 None, _create_announcement,
                 "announcement-deleted", id="announcement-delete"),
]


@pytest.mark.parametrize("method,path,body,setup,expected_action", MUTATIONS)
def test_mutation_writes_audit_row(client, auth_headers, mutation_guards,
                                   method, path, body, setup, expected_action):
    if setup is not None:
        setup(client, auth_headers)

    kwargs = {"headers": auth_headers}
    if body is not None:
        kwargs["json"] = body
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code < 400, (
        f"{method.upper()} {path} failed: {resp.status_code} {resp.text[:200]}"
    )

    audit_resp = client.get("/api/audit", headers=auth_headers)
    assert audit_resp.status_code == 200
    rows = audit_resp.json()
    matches = [r for r in rows if r.get("action") == expected_action]
    assert matches, (
        f"no audit row with action={expected_action!r} after "
        f"{method.upper()} {path}; got actions={[r.get('action') for r in rows]}"
    )
    # The actor must be recorded (DEV_MODE token resolves to 'dev').
    assert matches[0].get("admin_user"), "audit row missing admin_user"
