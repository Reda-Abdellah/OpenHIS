"""
Phase 8 — Admin Dashboard Tests (30 tests)
Covers: health, auth, session management, users CRUD,
        service health (mocked), config, audit log, announcements.
"""

# ─────────────────────────────────────────────────────────────────────────────
# TestHealth
# ─────────────────────────────────────────────────────────────────────────────
class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        j = r.json()
        assert j["status"]  == "ok"
        assert j["service"] == "admin"

    def test_health_counts_admin_users(self, client):
        r = client.get("/api/health")
        assert r.json()["admin_users"] >= 1

    def test_health_active_sessions(self, client, token):
        r = client.get("/api/health")
        assert r.json()["active_sessions"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# TestSecurity
# ─────────────────────────────────────────────────────────────────────────────
class TestSecurity:
    def test_password_hash_verify_roundtrip(self):
        from security import hash_password, verify_password
        stored = hash_password("SecurePass1!")
        assert verify_password("SecurePass1!", stored)

    def test_wrong_password_fails(self):
        from security import hash_password, verify_password
        stored = hash_password("correct")
        assert not verify_password("wrong", stored)

    def test_hash_unique_each_call(self):
        from security import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2   # different salts

    def test_purge_removes_expired_sessions(self, fresh_db):
        import datetime
        from database import get_db
        from security import create_admin_session, purge_expired_sessions, validate_admin_session
        tok = create_admin_session(1, 'admin')
        past = (datetime.datetime.utcnow() -
                datetime.timedelta(hours=1)).isoformat(timespec='seconds')
        with get_db() as db:
            db.execute("UPDATE admin_sessions SET expires_at=?", (past,))
        purge_expired_sessions()
        assert validate_admin_session(tok) is None


# ─────────────────────────────────────────────────────────────────────────────
# TestAuth
# ─────────────────────────────────────────────────────────────────────────────
class TestAuth:
    def test_login_success(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "admin", "password": "admin123"})
        assert r.status_code == 200
        j = r.json()
        assert "token"    in j
        assert j["username"] == "admin"

    def test_login_wrong_password_returns_401(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

    def test_login_unknown_user_returns_401(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "nobody", "password": "x"})
        assert r.status_code == 401

    def test_login_missing_fields_returns_400(self, client):
        r = client.post("/api/auth/login", json={"username": "admin"})
        assert r.status_code == 400

    def test_validate_valid_token(self, client, auth):
        r = client.get("/api/auth/validate", headers=auth)
        assert r.status_code == 200
        assert r.json()["valid"] is True
        assert r.json()["username"] == "admin"

    def test_validate_invalid_token_returns_401(self, client):
        r = client.get("/api/auth/validate",
                       headers={"Authorization": "Bearer FAKE-TOKEN-XYZ"})
        assert r.status_code == 401

    def test_validate_no_header_returns_401(self, client):
        r = client.get("/api/auth/validate")
        assert r.status_code == 401

    def test_logout_invalidates_session(self, client, token, auth):
        client.post("/api/auth/logout", json={}, headers=auth)
        r = client.get("/api/auth/validate",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# TestUsers
# ─────────────────────────────────────────────────────────────────────────────
class TestUsers:
    def test_list_users_returns_seeded_admin(self, client, auth):
        r = client.get("/api/users", headers=auth)
        assert r.status_code == 200
        usernames = [u["username"] for u in r.json()]
        assert "admin" in usernames

    def test_list_users_no_password_field(self, client, auth):
        r = client.get("/api/users", headers=auth)
        for u in r.json():
            assert "password" not in u

    def test_create_user_returns_201(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "nurse1",
                              "password": "pass123",
                              "role": "admin"},
                        headers=auth)
        assert r.status_code == 201
        j = r.json()
        assert j["username"] == "nurse1"
        assert j["role"]     == "admin"
        assert "password"    not in j

    def test_create_duplicate_user_returns_409(self, client, auth):
        client.post("/api/users",
                    json={"username": "dupe", "password": "p1"},
                    headers=auth)
        r = client.post("/api/users",
                        json={"username": "dupe", "password": "p2"},
                        headers=auth)
        assert r.status_code == 409

    def test_create_user_missing_fields_returns_400(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "nopass"},
                        headers=auth)
        assert r.status_code == 400

    def test_delete_other_user(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "todel", "password": "pass123"},
                        headers=auth)
        uid = r.json()["id"]
        r2  = client.delete(f"/api/users/{uid}", headers=auth)
        assert r2.status_code == 204

    def test_cannot_delete_own_account(self, client, auth):
        from database import get_db
        with get_db() as db:
            uid = db.execute(
                "SELECT id FROM admin_users WHERE username='admin'"
            ).fetchone()["id"]
        r = client.delete(f"/api/users/{uid}", headers=auth)
        assert r.status_code == 403

    def test_change_password(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "pwtest", "password": "oldpass"},
                        headers=auth)
        uid = r.json()["id"]
        r2  = client.patch(f"/api/users/{uid}/password",
                           json={"password": "newpass123"},
                           headers=auth)
        assert r2.status_code == 200
        # New password must work
        r3 = client.post("/api/auth/login",
                         json={"username": "pwtest", "password": "newpass123"})
        assert r3.status_code == 200

    def test_change_password_too_short_returns_400(self, client, auth):
        r = client.post("/api/users",
                        json={"username": "pwshort", "password": "init123"},
                        headers=auth)
        uid = r.json()["id"]
        r2  = client.patch(f"/api/users/{uid}/password",
                           json={"password": "abc"},
                           headers=auth)
        assert r2.status_code == 400

    def test_users_requires_auth(self, client):
        r = client.get("/api/users")
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# TestConfig
# ─────────────────────────────────────────────────────────────────────────────
class TestConfig:
    def test_list_config_returns_defaults(self, client, auth):
        r = client.get("/api/config", headers=auth)
        assert r.status_code == 200
        keys = [c["key"] for c in r.json()]
        assert "maintenance_mode"       in keys
        assert "patient_portal_enabled" in keys
        assert "session_timeout_hours"  in keys

    def test_get_config_by_key(self, client, auth):
        r = client.get("/api/config/maintenance_mode", headers=auth)
        assert r.status_code == 200
        assert r.json()["value"] == "false"

    def test_set_config_value(self, client, auth):
        r = client.put("/api/config/maintenance_mode",
                       json={"value": "true"},
                       headers=auth)
        assert r.status_code == 200
        assert r.json()["value"]      == "true"
        assert r.json()["updated_by"] == "admin"

    def test_set_config_persists(self, client, auth):
        client.put("/api/config/radiology_sla_hours",
                   json={"value": "48"}, headers=auth)
        r = client.get("/api/config/radiology_sla_hours", headers=auth)
        assert r.json()["value"] == "48"

    def test_set_config_creates_new_key(self, client, auth):
        r = client.put("/api/config/custom_key_001",
                       json={"value": "hello"},
                       headers=auth)
        assert r.status_code == 200
        assert r.json()["value"] == "hello"

    def test_get_unknown_key_returns_404(self, client, auth):
        r = client.get("/api/config/does_not_exist_xyz", headers=auth)
        assert r.status_code == 404

    def test_set_config_writes_audit(self, client, auth):
        client.put("/api/config/maintenance_mode",
                   json={"value": "true"}, headers=auth)
        audit = client.get("/api/audit?action=config-changed",
                           headers=auth).json()
        assert any(a["target"] == "maintenance_mode" for a in audit)


# ─────────────────────────────────────────────────────────────────────────────
# TestAnnouncements
# ─────────────────────────────────────────────────────────────────────────────
class TestAnnouncements:
    def test_create_announcement_returns_201(self, client, auth):
        r = client.post("/api/announcements",
                        json={"title": "Maintenance Tonight",
                              "body":  "System will be down 02:00–04:00 UTC",
                              "severity": "warning"},
                        headers=auth)
        assert r.status_code == 201
        j = r.json()
        assert j["title"]    == "Maintenance Tonight"
        assert j["severity"] == "warning"
        assert j["active"]   == 1

    def test_list_announcements_active_only(self, client, auth):
        client.post("/api/announcements",
                    json={"title": "A1", "body": "Active"},
                    headers=auth)
        r1 = client.post("/api/announcements",
                         json={"title": "A2", "body": "Will deactivate"},
                         headers=auth)
        aid = r1.json()["id"]
        client.patch(f"/api/announcements/{aid}",
                     json={"active": 0}, headers=auth)
        r = client.get("/api/announcements?active_only=true", headers=auth)
        assert all(a["active"] == 1 for a in r.json())

    def test_deactivate_announcement(self, client, auth):
        r = client.post("/api/announcements",
                        json={"title": "T", "body": "B"},
                        headers=auth)
        aid = r.json()["id"]
        r2  = client.patch(f"/api/announcements/{aid}",
                           json={"active": 0}, headers=auth)
        assert r2.json()["active"] == 0

    def test_delete_announcement(self, client, auth):
        r = client.post("/api/announcements",
                        json={"title": "Del me", "body": "Bye"},
                        headers=auth)
        aid = r.json()["id"]
        r2  = client.delete(f"/api/announcements/{aid}", headers=auth)
        assert r2.status_code == 204

    def test_invalid_severity_returns_422(self, client, auth):
        r = client.post("/api/announcements",
                        json={"title": "T", "body": "B",
                              "severity": "catastrophe"},
                        headers=auth)
        assert r.status_code == 422

    def test_missing_title_returns_400(self, client, auth):
        r = client.post("/api/announcements",
                        json={"body": "No title here"},
                        headers=auth)
        assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# TestAuditLog
# ─────────────────────────────────────────────────────────────────────────────
class TestAuditLog:
    def test_login_creates_audit_entry(self, client):
        client.post("/api/auth/login",
                    json={"username": "admin", "password": "admin123"})
        tok  = client.post("/api/auth/login",
                           json={"username": "admin",
                                 "password": "admin123"}).json()["token"]
        auth = {"Authorization": f"Bearer {tok}"}
        r    = client.get("/api/audit", headers=auth)
        actions = [e["action"] for e in r.json()]
        assert "login" in actions

    def test_audit_filter_by_user(self, client, auth):
        r = client.get("/api/audit?admin_user=admin", headers=auth)
        assert all(e["admin_user"] == "admin" for e in r.json())

    def test_audit_filter_by_action(self, client, auth):
        client.post("/api/announcements",
                    json={"title": "X", "body": "Y"}, headers=auth)
        r = client.get("/api/audit?action=announcement-created", headers=auth)
        assert all(e["action"] == "announcement-created" for e in r.json())

    def test_audit_requires_auth(self, client):
        r = client.get("/api/audit")
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# TestServiceHealth  (mocked — no real containers needed)
# ─────────────────────────────────────────────────────────────────────────────
class TestServiceHealth:
    def test_services_endpoint_returns_list(self, client, auth):
        from unittest.mock import AsyncMock, patch
        mock = AsyncMock(return_value={
            "services":   [{"name": "EHR",  "status": "online",
                            "response_ms": 12, "path": "/ehr"}],
            "online":  1, "offline": 0, "degraded": 0, "total": 1,
            "checked_at": "2026-03-24T00:00:00",
        })
        with patch('routers.services.asyncio.gather',
                   AsyncMock(return_value=[
                       {"name": "EHR", "status": "online",
                        "response_ms": 12, "url": "http://ehr/health",
                        "path": "/ehr", "data": {"status": "ok"}}
                   ])):
            r = client.get("/api/services", headers=auth)
        assert r.status_code == 200
        j = r.json()
        assert "services"    in j
        assert "online"      in j
        assert "checked_at"  in j

    def test_services_requires_auth(self, client):
        r = client.get("/api/services")
        assert r.status_code == 401
