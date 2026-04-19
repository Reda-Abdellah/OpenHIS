"""
Admin — /api/identity/* user lifecycle tests.

The identity router is a façade over the Keycloak Admin REST API. These
tests stub the Keycloak client module so the admin service logic (routing,
role guards, request validation, event publishing) is exercised without a
live Keycloak instance.
"""
import sys
import pytest
from unittest.mock import AsyncMock


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_kc(monkeypatch):
    """
    Replace the admin service's `keycloak_client` module functions with
    async mocks. The conftest reimports admin modules per test, so this
    fixture grabs the freshly imported module from sys.modules.
    """
    import keycloak_client
    monkeypatch.setattr(keycloak_client, "create_user",
                        AsyncMock(return_value="kc-uuid-new"))
    monkeypatch.setattr(keycloak_client, "assign_roles", AsyncMock(return_value=None))
    monkeypatch.setattr(keycloak_client, "set_roles",    AsyncMock(return_value=None))
    monkeypatch.setattr(keycloak_client, "disable_user", AsyncMock(return_value=None))
    monkeypatch.setattr(keycloak_client, "get_user",
                        AsyncMock(return_value={"id": "kc-uuid-1", "username": "alice"}))
    return keycloak_client


@pytest.fixture
def mock_provisioning(monkeypatch):
    """Stub host-app provisioning to return an empty per-app result map."""
    import provisioning
    monkeypatch.setattr(provisioning, "provision_user",   AsyncMock(return_value={}))
    monkeypatch.setattr(provisioning, "deprovision_user", AsyncMock(return_value=None))
    return provisioning


# ── POST /api/identity/users ────────────────────────────────────────────────

class TestCreateUser:
    def test_create_user_returns_id_and_status(self, client, auth_headers,
                                               mock_kc, mock_provisioning):
        resp = client.post(
            "/api/identity/users",
            json={
                "username":           "alice",
                "email":              "alice@example.org",
                "first_name":         "Alice",
                "last_name":          "Liddell",
                "roles":              ["clinician"],
                "temporary_password": "changeme-99",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"]     == "kc-uuid-new"
        assert body["status"] == "created"
        assert "provisioned" in body

    def test_create_user_missing_field_is_422(self, client, auth_headers):
        """Pydantic validation: missing `roles` list."""
        resp = client.post(
            "/api/identity/users",
            json={
                "username":           "bob",
                "email":              "bob@example.org",
                "first_name":         "Bob",
                "last_name":          "Bravo",
                "temporary_password": "x",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_create_user_returns_503_when_keycloak_unreachable(
        self, client, auth_headers, monkeypatch
    ):
        import keycloak_client
        async def boom(*_a, **_kw):
            raise RuntimeError("keycloak offline")
        monkeypatch.setattr(keycloak_client, "create_user", boom)

        resp = client.post(
            "/api/identity/users",
            json={
                "username":           "charlie",
                "email":              "charlie@example.org",
                "first_name":         "Charlie",
                "last_name":          "Chaplin",
                "roles":              ["clinician"],
                "temporary_password": "x",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 503
        assert "Keycloak unavailable" in resp.json()["detail"]


# ── PATCH /api/identity/users/{id}/roles ────────────────────────────────────

class TestUpdateRoles:
    def test_update_roles_returns_updated(self, client, auth_headers, mock_kc):
        resp = client.patch(
            "/api/identity/users/kc-uuid-1/roles",
            json={"roles": ["radiologist", "clinician"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        mock_kc.set_roles.assert_awaited_once_with(
            "kc-uuid-1", ["radiologist", "clinician"]
        )

    def test_update_roles_returns_503_when_keycloak_unreachable(
        self, client, auth_headers, monkeypatch
    ):
        import keycloak_client
        async def boom(*_a, **_kw):
            raise RuntimeError("keycloak offline")
        monkeypatch.setattr(keycloak_client, "set_roles", boom)

        resp = client.patch(
            "/api/identity/users/kc-uuid-1/roles",
            json={"roles": []},
            headers=auth_headers,
        )
        assert resp.status_code == 503


# ── DELETE /api/identity/users/{id} ─────────────────────────────────────────

class TestDeactivateUser:
    def test_delete_user_disables_and_deprovisions(
        self, client, auth_headers, mock_kc, mock_provisioning
    ):
        resp = client.delete(
            "/api/identity/users/kc-uuid-1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"
        mock_kc.disable_user.assert_awaited_once_with("kc-uuid-1")
        mock_provisioning.deprovision_user.assert_awaited_once_with("kc-uuid-1")


# ── GET /api/identity/users/{id} ────────────────────────────────────────────

class TestGetUser:
    def test_get_user_returns_payload(self, client, auth_headers, mock_kc):
        resp = client.get(
            "/api/identity/users/kc-uuid-1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "alice"

    def test_get_user_returns_404_when_missing(
        self, client, auth_headers, monkeypatch
    ):
        import keycloak_client
        from unittest.mock import AsyncMock
        monkeypatch.setattr(keycloak_client, "get_user", AsyncMock(return_value=None))

        resp = client.get(
            "/api/identity/users/unknown-uuid",
            headers=auth_headers,
        )
        assert resp.status_code == 404
