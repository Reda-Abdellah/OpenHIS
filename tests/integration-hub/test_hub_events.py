"""Integration Hub — event handler endpoint tests."""
import respx
import httpx


OMRS = "http://openmrs-hub-test:9998"
FHIR = f"{OMRS}/openmrs/ws/fhir2/R4"


class TestReportFinalEvent:
    def test_report_final_returns_queued(self, client):
        r = client.post("/api/events/report-final", json={
            "report_id": 5, "order_id": 10,
            "impression": "Normal study.", "status": "FINAL",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "queued"

    def test_report_final_missing_payload_still_queued(self, client):
        """Handler errors are best-effort — endpoint always returns queued."""
        r = client.post("/api/events/report-final", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "queued"


class TestDicomStoredEvent:
    def test_dicom_stored_returns_queued(self, client):
        r = client.post("/api/events/dicom-stored", json={
            "instanceId": "abc123", "patientId": "MRN-001",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "queued"

    def test_dicom_stored_without_instance_id_returns_queued(self, client):
        r = client.post("/api/events/dicom-stored", json={})
        assert r.status_code == 200


class TestAIJobCompletedEvent:
    def test_ai_job_completed_returns_queued(self, client):
        r = client.post("/api/events/ai-job-completed", json={
            "job_id": "abc123", "pipeline_id": "poc-xray",
            "patient_id": "MRN-001", "modality": "CR",
            "normal": True, "findings": [], "impression": "No acute findings.",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "queued"

    def test_ai_job_completed_without_job_id_returns_queued(self, client):
        r = client.post("/api/events/ai-job-completed", json={
            "pipeline_id": "poc-xray",
        })
        assert r.status_code == 200
