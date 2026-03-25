"""
Integration: patient registration cross-service flow.

Flow: EHR creates patient
       → fires POST /api/events/patient-created to FHIR bridge
         → FHIR bridge POSTs /api/patients/from-ehr to RIS
         → FHIR bridge POSTs /api/lab-patients to LIS
"""
import respx, httpx, pytest

RIS_BASE  = "http://ris:8002/api"
LIS_BASE  = "http://lis:8004/api"
FHIR_BASE = "http://fhir-bridge:8005"


# ── Phase 1: EHR → FHIR bridge ─────────────────────────────────────────────

class TestEHRFiresPatientCreatedEvent:
    """EHR must notify FHIR bridge when a patient is created."""

    def test_patient_create_calls_fhir_bridge(self, ehr_client):
        captured = {}

        def capture(req):
            import json
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"status": "queued"})

        with respx.mock:
            respx.post(f"{FHIR_BASE}/api/events/patient-created").mock(
                side_effect=capture
            )
            r = ehr_client.post("/api/patients", json={
                "mrn": "EHR001", "first_name": "Eve", "last_name": "Hospital",
                "birth_date": "1975-03-22", "sex": "F"
            })

        assert r.status_code == 201, r.text
        assert captured, "FHIR bridge was not called"
        body = captured["body"]
        assert body["mrn"] == "EHR001"
        assert body["first_name"] == "Eve"
        assert body["last_name"] == "Hospital"

    def test_patient_create_payload_includes_id(self, ehr_client):
        """The payload sent to FHIR bridge must include the generated patient id."""
        captured = {}

        def capture(req):
            import json
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"status": "queued"})

        with respx.mock:
            respx.post(f"{FHIR_BASE}/api/events/patient-created").mock(
                side_effect=capture
            )
            r = ehr_client.post("/api/patients", json={
                "mrn": "EHR002", "first_name": "Bob", "last_name": "Smith",
            })

        assert r.status_code == 201
        assert "id" in captured["body"], "patient id must be in FHIR bridge payload"
        assert captured["body"]["id"] == r.json()["id"]

    def test_fhir_bridge_failure_does_not_fail_patient_create(self, ehr_client):
        """FHIR bridge outage must NOT prevent patient creation."""
        with respx.mock:
            respx.post(f"{FHIR_BASE}/api/events/patient-created").mock(
                return_value=httpx.Response(503, text="unavailable")
            )
            r = ehr_client.post("/api/patients", json={
                "mrn": "EHR003", "first_name": "Charlie", "last_name": "Brown",
            })
        assert r.status_code == 201, "Patient create must succeed even when FHIR bridge is down"

    def test_no_fhir_call_when_bridge_url_not_set(self, tmp_path, monkeypatch):
        """When FHIR_BRIDGE_URL is empty, no outbound call is made."""
        from tests.integration.conftest import _clear_service_modules, _load
        db = str(tmp_path / "ehr_nofhir.db")
        monkeypatch.setenv("FHIR_BRIDGE_URL", "")
        _clear_service_modules()
        app = _load("ehr", {
            "DB_PATH": db, "DBPATH": db, "ROOT_PATH": "",
            "FHIR_BRIDGE_URL": "",
        })
        from fastapi.testclient import TestClient
        client = TestClient(app)

        with respx.mock:
            # If any unexpected call is made, respx raises
            r = client.post("/api/patients", json={
                "mrn": "EHR004", "first_name": "Diana", "last_name": "Prince",
            })
        assert r.status_code == 201


# ── Phase 2: FHIR bridge → RIS + LIS ───────────────────────────────────────

class TestFHIRBridgeRoutesPatientToDownstream:
    """FHIR bridge must fan out patient-created events to RIS and LIS."""

    PATIENT_PAYLOAD = {
        "id": "P-001", "mrn": "INT001",
        "first_name": "Alice", "last_name": "Wonder",
        "birth_date": "1990-01-01", "sex": "F"
    }

    def test_patient_created_calls_ris(self, fhir_client):
        captured_ris = {}

        def capture_ris(req):
            import json
            captured_ris["body"] = json.loads(req.content)
            return httpx.Response(200, json={"action": "created"})

        with respx.mock:
            respx.post(f"{RIS_BASE}/patients/from-ehr").mock(side_effect=capture_ris)
            respx.post(f"{LIS_BASE}/lab-patients").mock(
                return_value=httpx.Response(201, json={"id": 1})
            )
            r = fhir_client.post("/api/events/patient-created",
                                 json=self.PATIENT_PAYLOAD)

        assert r.status_code in (200, 202), r.text
        assert captured_ris, "RIS was not notified of new patient"
        body = captured_ris["body"]
        assert body["mrn"] == "INT001"
        assert "patient_name" in body  # RIS expects patient_name, not first/last

    def test_patient_created_ris_payload_format(self, fhir_client):
        """Verify the exact RIS payload — must have ehr_id, mrn, patient_name."""
        captured = {}

        with respx.mock:
            respx.post(f"{RIS_BASE}/patients/from-ehr").mock(
                side_effect=lambda req: (
                    captured.update({"body": __import__("json").loads(req.content)})
                    or httpx.Response(200, json={"action": "created"})
                )
            )
            respx.post(f"{LIS_BASE}/lab-patients").mock(
                return_value=httpx.Response(201, json={"id": 1})
            )
            fhir_client.post("/api/events/patient-created",
                             json=self.PATIENT_PAYLOAD)

        body = captured["body"]
        assert body.get("ehr_id") == "P-001"
        assert body.get("mrn") == "INT001"
        assert body.get("patient_name") == "Wonder, Alice"

    def test_patient_created_calls_lis(self, fhir_client):
        captured_lis = {}

        def capture_lis(req):
            import json
            captured_lis["body"] = json.loads(req.content)
            return httpx.Response(201, json={"id": 1})

        with respx.mock:
            respx.post(f"{RIS_BASE}/patients/from-ehr").mock(
                return_value=httpx.Response(200, json={"action": "created"})
            )
            respx.post(f"{LIS_BASE}/lab-patients").mock(side_effect=capture_lis)
            fhir_client.post("/api/events/patient-created",
                             json=self.PATIENT_PAYLOAD)

        assert captured_lis, "LIS was not notified of new patient"
        body = captured_lis["body"]
        assert body["mrn"] == "INT001"
        assert "patient_name" in body
        assert "ehr_patient_id" in body

    def test_patient_created_lis_payload_format(self, fhir_client):
        """LIS patient upsert must have ehr_patient_id, patient_name (Last, First), mrn."""
        captured = {}

        with respx.mock:
            respx.post(f"{RIS_BASE}/patients/from-ehr").mock(
                return_value=httpx.Response(200, json={"action": "created"})
            )
            respx.post(f"{LIS_BASE}/lab-patients").mock(
                side_effect=lambda req: (
                    captured.update({"body": __import__("json").loads(req.content)})
                    or httpx.Response(201, json={"id": 1})
                )
            )
            fhir_client.post("/api/events/patient-created",
                             json=self.PATIENT_PAYLOAD)

        body = captured["body"]
        assert body.get("ehr_patient_id") == "P-001"
        assert body.get("mrn") == "INT001"
        # name format is "Last, First"
        assert "Wonder" in body.get("patient_name", "")
        assert "Alice" in body.get("patient_name", "")


# ── Phase 3: RIS accepts patient from FHIR bridge ──────────────────────────

class TestRISAcceptsPatientFromEHR:
    """RIS /api/patients/from-ehr must correctly create/update patients."""

    def test_ris_creates_new_patient(self, ris_client):
        r = ris_client.post("/api/patients/from-ehr", json={
            "ehr_id": "P-001", "mrn": "INT001",
            "patient_name": "Wonder, Alice",
            "birth_date": "1990-01-01", "sex": "F"
        })
        assert r.status_code == 200
        assert r.json()["action"] == "created"

    def test_ris_updates_existing_patient(self, ris_client):
        # Create first
        ris_client.post("/api/patients/from-ehr", json={
            "ehr_id": "P-001", "mrn": "INT001",
            "patient_name": "Wonder, Alice",
        })
        # Upsert again with updated name
        r = ris_client.post("/api/patients/from-ehr", json={
            "ehr_id": "P-001", "mrn": "INT001",
            "patient_name": "Wonderland, Alice",
        })
        assert r.status_code == 200
        assert r.json()["action"] == "updated"

    def test_ris_patient_appears_in_worklist_after_create(self, ris_client):
        ris_client.post("/api/patients/from-ehr", json={
            "ehr_id": "P-002", "mrn": "INT002",
            "patient_name": "Doe, John",
        })
        r = ris_client.get("/api/patients?q=INT002")
        assert r.status_code == 200
        pts = r.json()
        assert len(pts) == 1
        assert pts[0]["mrn"] == "INT002"


# ── Phase 4: LIS accepts patient from FHIR bridge ──────────────────────────

class TestLISAcceptsPatientFromEHR:
    """LIS /api/lab-patients must upsert correctly."""

    def test_lis_creates_new_patient(self, lis_client):
        r = lis_client.post("/api/lab-patients", json={
            "ehr_patient_id": "P-001", "mrn": "INT001",
            "patient_name": "Wonder, Alice",
            "birth_date": "1990-01-01"
        })
        assert r.status_code == 201
        assert r.json()["mrn"] == "INT001"

    def test_lis_upserts_existing_patient(self, lis_client):
        lis_client.post("/api/lab-patients", json={
            "ehr_patient_id": "P-001", "mrn": "INT001",
            "patient_name": "Wonder, Alice"
        })
        # Upsert same MRN
        r = lis_client.post("/api/lab-patients", json={
            "ehr_patient_id": "P-001", "mrn": "INT001",
            "patient_name": "Wonderland, Alice"
        })
        assert r.status_code == 201
        assert r.json()["patient_name"] == "Wonderland, Alice"
