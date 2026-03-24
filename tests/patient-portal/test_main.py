"""
Phase 7 — Patient Portal Tests (28 tests)
Covers: health, auth (login/logout/validate), session management,
        all /api/me/* endpoints (mocked EHR/RIS), appointment requests.
"""
from unittest.mock import AsyncMock, patch
import datetime


# ── Mock EHR data ──────────────────────────────────────────────────────────────
MOCK_PATIENT = {
    "id": "P-001", "mrn": "MRN-001",
    "firstname": "Jane", "lastname": "Doe",
    "birthdate": "1985-03-15", "sex": "F",
    "phone": "555-0100", "insuranceid": "INS-001",
}
MOCK_APPOINTMENTS = [
    {"id": 1, "patientid": "P-001", "provider": "Dr. Smith",
     "department": "Cardiology", "scheduleddate": "2026-04-10T09:00:00",
     "durationminutes": 30, "status": "scheduled"},
]
MOCK_ORDERS = [
    {"id": 1, "ordertype": "LAB", "patientid": "P-001",
     "status": "COMPLETED", "priority": "ROUTINE",
     "orderdetail": '{"testcode":"CBC"}',
     "createdat": "2026-03-01T08:00:00",
     "updatedat": "2026-03-01T10:00:00"},
    {"id": 2, "ordertype": "LAB", "patientid": "P-001",
     "status": "PENDING", "priority": "STAT",
     "orderdetail": '{"testcode":"BMP"}',
     "createdat": "2026-03-24T07:00:00",
     "updatedat": "2026-03-24T07:00:00"},
    {"id": 3, "ordertype": "IMAGING", "patientid": "P-001",
     "status": "COMPLETED", "priority": "ROUTINE",
     "orderdetail": '{"modality":"CT"}',
     "createdat": "2026-03-10T08:00:00",
     "updatedat": "2026-03-10T12:00:00"},
]
MOCK_BILLING = [
    {"id": 1, "patientid": "P-001", "cptcode": "99213",
     "description": "Office Visit", "amount": 150.0,
     "status": "paid",    "createdat": "2026-03-01T00:00:00"},
    {"id": 2, "patientid": "P-001", "cptcode": "71046",
     "description": "Chest X-Ray",  "amount": 280.0,
     "status": "pending", "createdat": "2026-03-10T00:00:00"},
]
MOCK_DIAGNOSES = [
    {"id": 1, "patientid": "P-001", "icd10code": "I10",
     "description": "Essential Hypertension",
     "status": "active", "createdat": "2026-01-15T00:00:00"},
]
MOCK_ALLERGIES = [
    {"id": 1, "patientid": "P-001", "substance": "Penicillin",
     "reaction": "Rash", "severity": "mild"},
]
MOCK_REPORT = {
    "id": 1, "orderid": 3, "status": "FINAL",
    "impression": "No acute findings.",
    "recommendation": "Follow-up in 12 months.",
    "finalizedat": "2026-03-10T14:00:00",
}


def _proxy_side_effect(url):
    if '/diagnoses'    in url:    return MOCK_DIAGNOSES
    if '/allergies'    in url:    return MOCK_ALLERGIES
    if '/patients?q=' in url or '/patients/' in url:
        if '/patients/' in url:
            return MOCK_PATIENT
        return [MOCK_PATIENT]
    if '/appointments' in url:    return MOCK_APPOINTMENTS
    if '/orders' in url:
        if 'ordertype=LAB' in url:
            return [o for o in MOCK_ORDERS if o['ordertype'] == 'LAB']
        if 'ordertype=IMAGING' in url:
            return [o for o in MOCK_ORDERS if o['ordertype'] == 'IMAGING']
        return MOCK_ORDERS
    if '/billing'      in url:    return MOCK_BILLING
    if '/reports/order' in url:   return MOCK_REPORT
    return None


# ─────────────────────────────────────────────────────────────────────────────
# TestHealth
# ─────────────────────────────────────────────────────────────────────────────
class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        j = r.json()
        assert j["status"]  == "ok"
        assert j["service"] == "patient-portal"

    def test_health_counts_sessions(self, client):
        from auth import create_session
        create_session("P-001", "MRN-001", "Jane Doe")
        r = client.get("/api/health")
        assert r.json()["active_sessions"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# TestAuth
# ─────────────────────────────────────────────────────────────────────────────
class TestAuth:
    def test_login_success(self, client):
        mock = AsyncMock(side_effect=_proxy_side_effect)
        with patch('routers.auth.proxy_get', mock):
            r = client.post("/api/auth/login",
                            json={"mrn": "MRN-001", "birthdate": "1985-03-15"})
        assert r.status_code == 200
        j = r.json()
        assert "token"        in j
        assert j["patient_name"] == "Jane Doe"

    def test_login_wrong_dob_returns_401(self, client):
        mock = AsyncMock(side_effect=_proxy_side_effect)
        with patch('routers.auth.proxy_get', mock):
            r = client.post("/api/auth/login",
                            json={"mrn": "MRN-001", "birthdate": "1990-01-01"})
        assert r.status_code == 401

    def test_login_unknown_mrn_returns_401(self, client):
        mock = AsyncMock(return_value=[])
        with patch('routers.auth.proxy_get', mock):
            r = client.post("/api/auth/login",
                            json={"mrn": "MRN-UNKNOWN", "birthdate": "1985-03-15"})
        assert r.status_code == 401

    def test_login_missing_fields_returns_400(self, client):
        r = client.post("/api/auth/login", json={"mrn": "MRN-001"})
        assert r.status_code == 400

    def test_logout_clears_session(self, client):
        from auth import create_session
        token = create_session("P-001", "MRN-001", "Jane Doe")
        client.post("/api/auth/logout", json={"token": token})
        r = client.get("/api/auth/validate",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_validate_valid_token(self, client):
        from auth import create_session
        token = create_session("P-001", "MRN-001", "Jane Doe")
        r = client.get("/api/auth/validate",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["valid"] is True

    def test_validate_invalid_token_returns_401(self, client):
        r = client.get("/api/auth/validate",
                       headers={"Authorization": "Bearer FAKE-TOKEN"})
        assert r.status_code == 401

    def test_validate_no_header_returns_401(self, client):
        r = client.get("/api/auth/validate")
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# TestSessionManagement
# ─────────────────────────────────────────────────────────────────────────────
class TestSessionManagement:
    def test_expired_session_rejected(self, client, auth_headers):
        """Manually expire a session and verify it is rejected."""
        from database import get_db
        past = (datetime.datetime.utcnow() -
                datetime.timedelta(hours=1)).isoformat(timespec='seconds')
        with get_db() as db:
            db.execute("UPDATE sessions SET expires_at=?", (past,))
        r = client.get("/api/me", headers=auth_headers)
        assert r.status_code == 401

    def test_missing_auth_header_returns_401(self, client):
        r = client.get("/api/me")
        assert r.status_code == 401

    def test_malformed_bearer_returns_401(self, client):
        r = client.get("/api/me", headers={"Authorization": "Token abc123"})
        assert r.status_code == 401

    def test_purge_expired_removes_stale_sessions(self):
        from auth import create_session, purge_expired, validate_session
        from database import get_db
        token = create_session("P-001", "MRN-001", "Jane")
        past  = (datetime.datetime.utcnow() -
                 datetime.timedelta(hours=2)).isoformat(timespec='seconds')
        with get_db() as db:
            db.execute("UPDATE sessions SET expires_at=? WHERE id=?",
                       (past, token))
        purge_expired()
        assert validate_session(token) is None


# ─────────────────────────────────────────────────────────────────────────────
# TestMeEndpoints
# ─────────────────────────────────────────────────────────────────────────────
class TestMeEndpoints:
    def _mock(self):
        return AsyncMock(side_effect=_proxy_side_effect)

    def test_get_me_profile(self, client, auth_headers):
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me", headers=auth_headers)
        assert r.status_code == 200
        j = r.json()
        assert j["mrn"]       == "MRN-001"
        assert j["firstname"] == "Jane"
        assert "id" in j

    def test_get_me_no_internal_fields(self, client, auth_headers):
        """Sensitive internal fields must not leak to patient."""
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me", headers=auth_headers)
        j = r.json()
        assert "cdssalerts"  not in j
        assert "encounters"  not in j
        assert "diagnoses"   not in j

    def test_get_appointments(self, client, auth_headers):
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me/appointments", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_get_lab_results_only_completed(self, client, auth_headers):
        """Only COMPLETED lab orders visible — PENDING must be filtered out."""
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me/results", headers=auth_headers)
        results = r.json()
        assert all(x["status"] == "COMPLETED" for x in results)
        assert len(results) == 1    # only 1 COMPLETED LAB in mock data

    def test_get_imaging_with_report(self, client, auth_headers):
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me/imaging", headers=auth_headers)
        items = r.json()
        assert len(items) == 1
        assert items[0]["report"]["impression"] == "No acute findings."

    def test_get_diagnoses_active_only(self, client, auth_headers):
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me/diagnoses", headers=auth_headers)
        diags = r.json()
        assert len(diags) == 1
        assert diags[0]["icd10code"] == "I10"

    def test_get_allergies(self, client, auth_headers):
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me/allergies", headers=auth_headers)
        allgs = r.json()
        assert len(allgs) == 1
        assert allgs[0]["substance"] == "Penicillin"

    def test_get_billing(self, client, auth_headers):
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me/billing", headers=auth_headers)
        assert r.status_code == 200
        amounts = [b["amount"] for b in r.json()]
        assert 150.0 in amounts
        assert 280.0 in amounts

    def test_get_summary(self, client, auth_headers):
        with patch('routers.me.proxy.get', self._mock()):
            r = client.get("/api/me/summary", headers=auth_headers)
        j = r.json()
        assert "upcoming_appointments" in j
        assert "pending_results"       in j
        assert "total_due"             in j
        assert j["pending_results"]    == 1     # one PENDING LAB in mock
        assert j["total_due"]          == 280.0 # one pending bill

    def test_backend_down_returns_503(self, client, auth_headers):
        with patch('routers.me.proxy.get', AsyncMock(return_value=None)):
            r = client.get("/api/me", headers=auth_headers)
        assert r.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# TestAppointmentRequests
# ─────────────────────────────────────────────────────────────────────────────
class TestAppointmentRequests:
    def test_create_request_returns_201(self, client, auth_headers):
        r = client.post("/api/me/appointments/request",
                        json={"department": "Cardiology",
                              "preferred_date": "2026-05-01",
                              "reason": "Annual checkup"},
                        headers=auth_headers)
        assert r.status_code == 201
        j = r.json()
        assert j["status"]     == "ok"
        assert j["request_id"] > 0

    def test_create_request_missing_dept_returns_400(self, client, auth_headers):
        r = client.post("/api/me/appointments/request",
                        json={"preferred_date": "2026-05-01"},
                        headers=auth_headers)
        assert r.status_code == 400

    def test_list_own_requests(self, client, auth_headers):
        client.post("/api/me/appointments/request",
                    json={"department": "Neurology"},
                    headers=auth_headers)
        client.post("/api/me/appointments/request",
                    json={"department": "Cardiology"},
                    headers=auth_headers)
        r = client.get("/api/me/appointments/requests",
                       headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_requests_isolated_between_patients(self, client):
        """Patient A must not see Patient B's requests."""
        from auth import create_session
        token_a = create_session("P-001", "MRN-001", "Jane Doe")
        token_b = create_session("P-002", "MRN-002", "John Smith")
        hdr_a   = {"Authorization": f"Bearer {token_a}"}
        hdr_b   = {"Authorization": f"Bearer {token_b}"}
        client.post("/api/me/appointments/request",
                    json={"department": "Cardiology"}, headers=hdr_a)
        r = client.get("/api/me/appointments/requests", headers=hdr_b)
        assert r.json() == []
