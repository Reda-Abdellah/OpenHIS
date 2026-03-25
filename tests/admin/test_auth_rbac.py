"""
Auth / RBAC enforcement tests for the Admin service.

Covers:
- All protected endpoints return 401 without a token
- All protected endpoints return 401 with an invalid token
- A valid session can access protected endpoints
- Logout invalidates the token (subsequent requests → 401)
- Self-deletion is forbidden
- Password minimum-length validation
- Duplicate username prevention
- Session expiry
"""
import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

PROTECTED_GET  = ["/api/users", "/api/config", "/api/services", "/api/audit"]
PROTECTED_ENDPOINTS = [
    ("GET",    "/api/users"),
    ("GET",    "/api/config"),
    ("GET",    "/api/audit"),
    ("POST",   "/api/users"),
    ("PUT",    "/api/config/some-key"),
]


def _login(client, username="admin", password="admin123") -> str:
    r = client.post("/api/auth/login",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── missing / malformed token ─────────────────────────────────────────────────

class TestUnauthenticatedAccess:

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_no_token_returns_401(self, client, method, path):
        if method == "GET":
            r = client.get(path)
        elif method == "POST":
            r = client.post(path, json={})
        else:
            r = client.put(path, json={})
        assert r.status_code == 401, (
            f"{method} {path} returned {r.status_code} without auth"
        )

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_wrong_token_returns_401(self, client, method, path):
        headers = {"Authorization": "Bearer totally-invalid-token"}
        if method == "GET":
            r = client.get(path, headers=headers)
        elif method == "POST":
            r = client.post(path, json={}, headers=headers)
        else:
            r = client.put(path, json={}, headers=headers)
        assert r.status_code == 401

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_malformed_auth_header_returns_401(self, client, method, path):
        """Authorization header without 'Bearer ' prefix is rejected."""
        headers = {"Authorization": "Token abc123"}
        if method == "GET":
            r = client.get(path, headers=headers)
        elif method == "POST":
            r = client.post(path, json={}, headers=headers)
        else:
            r = client.put(path, json={}, headers=headers)
        assert r.status_code == 401

    def test_empty_auth_header_returns_401(self, client):
        r = client.get("/api/users", headers={"Authorization": ""})
        assert r.status_code == 401

    def test_health_endpoint_is_public(self, client):
        """Health check must be accessible without auth."""
        r = client.get("/api/health")
        assert r.status_code == 200


# ── valid session ─────────────────────────────────────────────────────────────

class TestValidSession:

    def test_valid_token_allows_list_users(self, client, auth):
        r = client.get("/api/users", headers=auth)
        assert r.status_code == 200
        users = r.json()
        usernames = [u["username"] for u in users]
        assert "admin" in usernames

    def test_valid_token_allows_list_config(self, client, auth):
        r = client.get("/api/config", headers=auth)
        assert r.status_code == 200

    def test_validate_endpoint_returns_username(self, client, auth):
        r = client.get("/api/auth/validate", headers=auth)
        assert r.status_code == 200
        assert r.json()["username"] == "admin"
        assert r.json()["valid"] is True

    def test_response_does_not_include_password_hash(self, client, auth):
        r = client.get("/api/users", headers=auth)
        for user in r.json():
            assert "password" not in user, "Password hash must never be returned"


# ── login / logout ────────────────────────────────────────────────────────────

class TestLoginLogout:

    def test_login_wrong_password_returns_401(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "admin", "password": "wrongpass"})
        assert r.status_code == 401

    def test_login_nonexistent_user_returns_401(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "ghost", "password": "whatever"})
        assert r.status_code == 401

    def test_login_empty_credentials_returns_400(self, client):
        r = client.post("/api/auth/login", json={"username": "", "password": ""})
        assert r.status_code == 400

    def test_login_returns_token_and_role(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "admin", "password": "admin123"})
        assert r.status_code == 200
        body = r.json()
        assert "token" in body
        assert body["role"] == "superadmin"
        assert len(body["token"]) > 10

    def test_logout_invalidates_token(self, client):
        token = _login(client)
        headers = _auth(token)

        # Verify the token works
        assert client.get("/api/users", headers=headers).status_code == 200

        # Logout
        r = client.post("/api/auth/logout", json={"token": token}, headers=headers)
        assert r.status_code == 200

        # Token must now be invalid
        r2 = client.get("/api/users", headers=headers)
        assert r2.status_code == 401, "Token should be invalid after logout"

    def test_multiple_logins_create_independent_sessions(self, client):
        token1 = _login(client)
        token2 = _login(client)
        assert token1 != token2
        # Both must work independently
        assert client.get("/api/users", headers=_auth(token1)).status_code == 200
        assert client.get("/api/users", headers=_auth(token2)).status_code == 200


# ── user management (auth-gated) ──────────────────────────────────────────────

class TestUserManagement:

    def test_create_user_requires_auth(self, client):
        r = client.post("/api/users", json={"username": "eve", "password": "pass123"})
        assert r.status_code == 401

    def test_create_user_with_valid_auth(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "newuser", "password": "secure123", "role": "admin"},
                        headers=auth)
        assert r.status_code == 201
        body = r.json()
        assert body["username"] == "newuser"
        assert body["role"] == "admin"
        assert "password" not in body

    def test_create_duplicate_username_returns_409(self, client, auth):
        client.post("/api/users",
                    json={"username": "dupuser", "password": "pass1234"},
                    headers=auth)
        r = client.post("/api/users",
                        json={"username": "dupuser", "password": "other1234"},
                        headers=auth)
        assert r.status_code == 409

    def test_delete_other_user(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "todelete", "password": "pass1234"},
                        headers=auth)
        uid = r.json()["id"]
        r2  = client.delete(f"/api/users/{uid}", headers=auth)
        assert r2.status_code == 204

    def test_cannot_delete_own_account(self, client, auth):
        """Admin must not be able to delete their own account."""
        # Get admin's user id
        users = client.get("/api/users", headers=auth).json()
        admin = next(u for u in users if u["username"] == "admin")
        r = client.delete(f"/api/users/{admin['id']}", headers=auth)
        assert r.status_code == 403

    def test_change_password_too_short_returns_400(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "shortpw", "password": "pass1234"},
                        headers=auth)
        uid = r.json()["id"]
        r2  = client.patch(f"/api/users/{uid}/password",
                           json={"password": "abc"},
                           headers=auth)
        assert r2.status_code == 400

    def test_change_password_valid(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "pwchange", "password": "oldpass1"},
                        headers=auth)
        uid = r.json()["id"]
        r2  = client.patch(f"/api/users/{uid}/password",
                           json={"password": "newpass999"},
                           headers=auth)
        assert r2.status_code == 200

    def test_change_password_requires_auth(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "nopwchange", "password": "pass1234"},
                        headers=auth)
        uid = r.json()["id"]
        r2  = client.patch(f"/api/users/{uid}/password",
                           json={"password": "newpass999"})
        assert r2.status_code == 401

    def test_delete_nonexistent_user_returns_404(self, client, auth):
        r = client.delete("/api/users/999999", headers=auth)
        assert r.status_code == 404


# ── config management (auth-gated) ────────────────────────────────────────────

class TestConfigManagement:

    def test_set_config_requires_auth(self, client):
        r = client.put("/api/config/test-key", json={"value": "testval"})
        assert r.status_code == 401

    def test_set_and_get_config(self, client, auth):
        r = client.put("/api/config/test.setting",
                       json={"value": "hello"},
                       headers=auth)
        assert r.status_code == 200
        assert r.json()["value"] == "hello"

        r2 = client.get("/api/config/test.setting", headers=auth)
        assert r2.status_code == 200
        assert r2.json()["value"] == "hello"

    def test_get_nonexistent_config_returns_404(self, client, auth):
        r = client.get("/api/config/does-not-exist-xyz", headers=auth)
        assert r.status_code == 404


# ── audit log (auth-gated) ────────────────────────────────────────────────────

class TestAuditLog:

    def test_audit_log_requires_auth(self, client):
        r = client.get("/api/audit")
        assert r.status_code == 401

    def test_login_creates_audit_entry(self, client):
        # Use the HTTP login endpoint so an audit entry is created
        token = _login(client)
        r = client.get("/api/audit", headers=_auth(token))
        assert r.status_code == 200
        entries = r.json()
        actions = [e["action"] for e in entries]
        assert "login" in actions

    def test_user_creation_creates_audit_entry(self, client):
        token = _login(client)
        headers = _auth(token)
        client.post("/api/users",
                    json={"username": "auditme", "password": "pass1234"},
                    headers=headers)
        r = client.get("/api/audit", headers=headers)
        actions = [e["action"] for e in r.json()]
        assert "user-created" in actions


# ── session expiry ────────────────────────────────────────────────────────────

class TestSessionExpiry:

    def test_expired_session_returns_401(self, tmp_path, monkeypatch):
        """A session with TTL=0 should immediately expire."""
        db = str(tmp_path / "admin_expiry.db")
        monkeypatch.setenv("DB_PATH", db)
        monkeypatch.setenv("SESSION_TTL_HOURS", "0")  # instant expiry
        monkeypatch.setenv("ADMIN_USER", "admin")
        monkeypatch.setenv("ADMIN_PASS", "admin123")

        import sys
        # Use the same broad clearing as the admin conftest to ensure
        # SESSION_TTL_HOURS is re-read from env on fresh import
        to_clear = [k for k in sys.modules
                    if k.startswith(('admin_', 'routers', 'security', 'database'))
                    or k in ('main', 'auth', 'users', 'services', 'config',
                             'announcements', 'audit')]
        for mod in to_clear:
            del sys.modules[mod]

        admin_path = str(
            __import__('pathlib').Path(__file__).parent.parent.parent
            / "services" / "admin"
        )
        if admin_path in sys.path:
            sys.path.remove(admin_path)
        sys.path.insert(0, admin_path)

        from main import app
        from database import init_db
        import main as m
        init_db()
        m._seed_default_admin()

        from fastapi.testclient import TestClient
        c = TestClient(app)
        token = _login(c)
        # With TTL=0 the session expires at the moment it's created
        r = c.get("/api/users", headers=_auth(token))
        assert r.status_code == 401, "Session with TTL=0 must be immediately expired"
