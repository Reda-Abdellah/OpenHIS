"""
Patient Portal Tests
Covers: health, auth (login/logout/validate), session management,
        all /api/me/* endpoints (OpenMRS/OpenELIS FHIR mocked via respx),
        appointment requests.
"""
import datetime
from unittest.mock import AsyncMock, patch
import respx
import httpx

# ── FHIR mock data ────────────────────────────────────────────────────────────

_OMRS = "http://openmrs-test:9999/openmrs/ws/fhir2/R4"
_OE   = "http://openelis-test:9999/fhir/R4"

FHIR_PATIENT_BUNDLE = {
    "resourceType": "Bundle",
    "entry": [{
        "resource": {
            "id": "P-001",
            "resourceType": "Patient",
            "birthDate": "1985-03-15",
            "gender": "female",
            "name": [{"given": ["Jane"], "family": "Doe"}],
            "identifier": [{"value": "MRN-001"}],
        }
    }]
}

FHIR_PATIENT = FHIR_PATIENT_BUNDLE["entry"][0]["resource"]

FHIR_ENCOUNTER_BUNDLE = {
    "resourceType": "Bundle",
    "entry": [{
        "resource": {
            "id": "enc-001", "status": "in-progress",
            "period": {"start": "2026-04-10T09:00:00"},
            "type": [{"text": "Cardiology Visit"}],
        }
    }]
}

FHIR_DR_BUNDLE = {
    "resourceType": "Bundle",
    "total": 1,
    "entry": [{
        "resource": {
            "id": "dr-001", "status": "final",
            "issued": "2026-03-01T10:00:00",
            "code": {"text": "CBC"},
            "conclusion": "Normal",
        }
    }]
}

FHIR_CONDITION_BUNDLE = {
    "resourceType": "Bundle",
    "entry": [{
        "resource": {
            "id": "cond-001",
            "code": {
                "text": "Essential Hypertension",
                "coding": [{"code": "I10"}],
            },
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "recordedDate": "2026-01-15",
        }
    }]
}

FHIR_ALLERGY_BUNDLE = {
    "resourceType": "Bundle",
    "entry": [{
        "resource": {
            "id": "ai-001",
            "code": {"text": "Penicillin"},
            "reaction": [{"manifestation": [{"text": "Rash"}], "severity": "mild"}],
        }
    }]
}

MOCK_RIS_ORDERS = [
    {"id": 1, "modality": "CT", "body_part": "CHEST", "mrn": "MRN-001",
     "status": "COMPLETED", "accession_number": "ACC-001",
     "created_at": "2026-03-10T08:00:00", "updated_at": "2026-03-10T12:00:00"},
]
MOCK_RIS_REPORT = {
    "id": 1, "order_id": 1, "status": "FINAL",
    "impression": "No acute findings.",
    "recommendation": "Follow-up in 12 months.",
    "finalized_at": "2026-03-10T14:00:00",
}


def _fhir_side_effect(url, params=None):
    """Mock for routers.me._fhir_get based on URL.
    Signature matches _fhir_get(url, params=None).
    """
    if "/Patient/P-001" in url:
        return FHIR_PATIENT
    if "/Encounter" in url:
        return FHIR_ENCOUNTER_BUNDLE
    if "/DiagnosticReport" in url:
        return FHIR_DR_BUNDLE
    if "/Condition" in url:
        return FHIR_CONDITION_BUNDLE
    if "/AllergyIntolerance" in url:
        return FHIR_ALLERGY_BUNDLE
    return {}


def _fhir_summary_side_effect(url, params=None):
    """Summary endpoint needs planned encounters and count-only DR bundle."""
    if "/Encounter" in url:
        return FHIR_ENCOUNTER_BUNDLE
    if "/DiagnosticReport" in url:
        return {"resourceType": "Bundle", "total": 2, "entry": []}
    return {}


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
    @respx.mock
    def test_login_success(self, client):
        respx.get(f"{_OMRS}/Patient").mock(
            return_value=httpx.Response(200, json=FHIR_PATIENT_BUNDLE)
        )
        r = client.post("/api/auth/login",
                        json={"mrn": "MRN-001", "birthdate": "1985-03-15"})
        assert r.status_code == 200
        j = r.json()
        assert "token"        in j
        assert j["patient_name"] == "Jane Doe"

    @respx.mock
    def test_login_wrong_dob_returns_401(self, client):
        respx.get(f"{_OMRS}/Patient").mock(
            return_value=httpx.Response(200, json=FHIR_PATIENT_BUNDLE)
        )
        r = client.post("/api/auth/login",
                        json={"mrn": "MRN-001", "birthdate": "1990-01-01"})
        assert r.status_code == 401

    @respx.mock
    def test_login_unknown_mrn_returns_401(self, client):
        empty_bundle = {"resourceType": "Bundle", "entry": []}
        respx.get(f"{_OMRS}/Patient").mock(
            return_value=httpx.Response(200, json=empty_bundle)
        )
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
    def test_get_me_profile(self, client, auth_headers):
        mock = AsyncMock(side_effect=_fhir_side_effect)
        with patch('routers.me._fhir_get', mock):
            r = client.get("/api/me", headers=auth_headers)
        assert r.status_code == 200
        j = r.json()
        assert j["mrn"]       == "MRN-001"
        assert j["firstname"] == "Jane"
        assert "id" in j

    def test_get_me_no_internal_fields(self, client, auth_headers):
        mock = AsyncMock(side_effect=_fhir_side_effect)
        with patch('routers.me._fhir_get', mock):
            r = client.get("/api/me", headers=auth_headers)
        j = r.json()
        assert "cdssalerts" not in j
        assert "encounters"  not in j

    def test_get_appointments(self, client, auth_headers):
        mock = AsyncMock(side_effect=_fhir_side_effect)
        with patch('routers.me._fhir_get', mock):
            r = client.get("/api/me/appointments", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_get_lab_results_all_final(self, client, auth_headers):
        """Results endpoint queries status=final — all returned entries are final."""
        mock = AsyncMock(side_effect=_fhir_side_effect)
        with patch('routers.me._fhir_get', mock):
            r = client.get("/api/me/results", headers=auth_headers)
        results = r.json()
        assert len(results) == 1
        assert results[0]["status"] == "final"

    def test_get_imaging_with_report(self, client, auth_headers):
        ris_mock = AsyncMock(side_effect=lambda url: (
            MOCK_RIS_ORDERS if "/orders" in url and "/reports" not in url
            else MOCK_RIS_REPORT
        ))
        with patch('routers.me.proxy.get', ris_mock):
            r = client.get("/api/me/imaging", headers=auth_headers)
        items = r.json()
        assert len(items) == 1
        assert items[0]["report"]["impression"] == "No acute findings."

    def test_get_diagnoses(self, client, auth_headers):
        mock = AsyncMock(side_effect=_fhir_side_effect)
        with patch('routers.me._fhir_get', mock):
            r = client.get("/api/me/diagnoses", headers=auth_headers)
        diags = r.json()
        assert len(diags) == 1
        assert diags[0]["icd10code"] == "I10"

    def test_get_allergies(self, client, auth_headers):
        mock = AsyncMock(side_effect=_fhir_side_effect)
        with patch('routers.me._fhir_get', mock):
            r = client.get("/api/me/allergies", headers=auth_headers)
        allgs = r.json()
        assert len(allgs) == 1
        assert allgs[0]["substance"] == "Penicillin"

    def test_get_billing_returns_empty(self, client, auth_headers):
        """Billing is a placeholder — always returns empty list."""
        r = client.get("/api/me/billing", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []

    def test_get_summary(self, client, auth_headers):
        mock = AsyncMock(side_effect=_fhir_summary_side_effect)
        with patch('routers.me._fhir_get', mock):
            r = client.get("/api/me/summary", headers=auth_headers)
        j = r.json()
        assert "upcoming_appointments" in j
        assert "pending_results"       in j
        assert "total_due"             in j
        assert j["upcoming_appointments"] == 1
        assert j["pending_results"]       == 2
        assert j["total_due"]             == 0.0

    def test_backend_down_returns_503(self, client, auth_headers):
        mock = AsyncMock(return_value={})
        with patch('routers.me._fhir_get', mock):
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
        for dept in ["Neurology", "Cardiology"]:
            client.post("/api/me/appointments/request",
                        json={"department": dept},
                        headers=auth_headers)
        r = client.get("/api/me/appointments/requests", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_requests_isolated_between_patients(self, client):
        from auth import create_session
        token_a = create_session("P-001", "MRN-001", "Jane Doe")
        token_b = create_session("P-002", "MRN-002", "John Smith")
        hdr_a   = {"Authorization": f"Bearer {token_a}"}
        hdr_b   = {"Authorization": f"Bearer {token_b}"}
        client.post("/api/me/appointments/request",
                    json={"department": "Cardiology"}, headers=hdr_a)
        r = client.get("/api/me/appointments/requests", headers=hdr_b)
        assert r.json() == []
