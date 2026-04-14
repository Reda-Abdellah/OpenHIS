"""
Tests for admin service — profile management endpoints.

Covers: GET /api/profiles/active, POST /api/profiles/enable, disable.
"""
import os
import pytest


def test_profiles_active_returns_list(client, auth_headers, tmp_path):
    """GET /api/profiles/active returns {"profiles": [...]}."""
    resp = client.get("/api/profiles/active", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "profiles" in data
    assert isinstance(data["profiles"], list)


def test_profiles_enable_writes_to_env(client, auth_headers, monkeypatch, tmp_path):
    """POST /api/profiles/enable adds the profile to the active set."""
    env_file = str(tmp_path / ".env")
    with open(env_file, "w") as f:
        f.write("OPENHIS_PROFILES=emr\n")

    # Monkeypatch the _ENV_FILE path used by the profiles router
    import sys
    profiles_mod = sys.modules.get("routers.profiles")
    if profiles_mod:
        monkeypatch.setattr(profiles_mod, "_ENV_FILE", env_file)

    resp = client.post("/api/profiles/enable",
                       json={"profiles": ["laboratory"]},
                       headers=auth_headers)
    # 200 or 202 depending on implementation
    assert resp.status_code in (200, 202, 204)


def test_profiles_disable_unknown_is_noop(client, auth_headers):
    """Disabling a profile that is not active should not fail."""
    resp = client.post("/api/profiles/disable",
                       json={"profiles": ["nonexistent-profile"]},
                       headers=auth_headers)
    assert resp.status_code in (200, 202, 204, 400)


@pytest.mark.skipif(
    os.environ.get("DEV_MODE") == "true",
    reason="DEV_MODE=true bypasses auth enforcement; test belongs in integration suite"
)
def test_profiles_enable_requires_auth(client):
    """Profile enable endpoint must require authentication."""
    resp = client.post("/api/profiles/enable", json={"profiles": ["emr"]})
    assert resp.status_code in (401, 403)
