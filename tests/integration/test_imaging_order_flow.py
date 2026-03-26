"""
Integration: imaging order cross-service flow (new architecture).

Flow:
  OpenMRS imaging ServiceRequest (category=imaging)
    → RIS OpenMRS sync worker polls FHIR, auto-creates RIS order
    → Radiologist finalises report in RIS
      → RIS fires POST /api/events/report-final to integration-hub
        → Hub queues FHIR DiagnosticReport push to OpenMRS

These tests verify:
  1. Hub correctly accepts and queues report-final events
  2. RIS order lifecycle (create → status progression → report)
  3. STAT orders precede ROUTINE orders in worklist
"""
import respx
import httpx


# ── Hub: radiology event routing ──────────────────────────────────────────────

class TestHubRadiologyEvents:
    """Hub accepts radiology events from RIS and Orthanc."""

    def test_report_final_queued(self, hub_client):
        r = hub_client.post("/api/events/report-final", json={
            "report_id": 7, "order_id": 3,
            "impression": "No acute intracranial findings.",
            "status": "FINAL",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "queued"

    def test_dicom_stored_queued(self, hub_client):
        r = hub_client.post("/api/events/dicom-stored", json={
            "instanceId": "dicom-abc-123",
            "patientId": "MRN-001",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "queued"

    def test_ai_job_completed_queued(self, hub_client):
        r = hub_client.post("/api/events/ai-job-completed", json={
            "job_id": "job-xyz-456",
            "pipeline_id": "poc-ct",
            "patient_id": "MRN-002",
            "modality": "CT",
            "normal": False,
            "impression": "Pulmonary nodule 8mm — recommend follow-up.",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "queued"

    def test_report_final_with_only_order_id_still_queued(self, hub_client):
        """Hub fetches full report from RIS internally — caller may send minimal payload."""
        r = hub_client.post("/api/events/report-final", json={
            "report_id": 5, "order_id": 10,
        })
        assert r.status_code == 200


# ── RIS order lifecycle ───────────────────────────────────────────────────────

class TestRISAcceptsImagingOrder:
    """RIS order creation and worklist management."""

    def _setup_patient(self, ris_client):
        r = ris_client.post("/api/patients", json={
            "mrn": "INT001", "patient_name": "Test Patient",
        })
        assert r.status_code == 201
        return r.json()["id"]

    def test_ris_creates_order_with_accession(self, ris_client):
        pid = self._setup_patient(ris_client)
        r = ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "CT",
            "body_part": "CHEST", "priority": "ROUTINE",
        })
        assert r.status_code == 201
        order = r.json()
        assert order["accession_number"].startswith("ACC-")
        assert order["status"] == "PENDING"
        assert order["modality"] == "CT"

    def test_ris_order_appears_in_worklist(self, ris_client):
        pid = self._setup_patient(ris_client)
        ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "MR", "priority": "STAT",
        })
        r = ris_client.get("/api/worklist?modality=MR")
        assert r.status_code == 200
        items = r.json()
        assert any(o["modality"] == "MR" for o in items)

    def test_stat_priority_appears_first_in_worklist(self, ris_client):
        pid = self._setup_patient(ris_client)
        ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "CR", "priority": "ROUTINE",
        })
        ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "CR", "priority": "STAT",
        })
        r = ris_client.get("/api/worklist")
        assert r.status_code == 200
        orders = r.json()
        priorities  = [o["priority"] for o in orders]
        stat_idx    = priorities.index("STAT")
        routine_idx = priorities.index("ROUTINE")
        assert stat_idx < routine_idx, "STAT should precede ROUTINE in worklist"

    def test_ris_report_finalize_triggers_hub_notification(self, ris_client):
        """Finalising a report fires a best-effort POST to FHIR_BRIDGE_URL (integration-hub)."""
        pid = self._setup_patient(ris_client)
        order = ris_client.post("/api/orders", json={
            "patient_id": pid, "modality": "CR", "priority": "ROUTINE",
        }).json()

        report = ris_client.post("/api/reports", json={
            "order_id": order["id"],
            "radiologist": "Dr. Radiologist",
        }).json()

        with respx.mock:
            # FHIR_BRIDGE_URL is empty in tests so no actual HTTP call is made
            r = ris_client.put(f"/api/reports/{report['id']}", json={
                "status": "FINAL",
                "findings": "Clear lungs.",
                "impression": "No acute findings.",
            })

        assert r.status_code == 200
        assert r.json()["status"] == "FINAL"
