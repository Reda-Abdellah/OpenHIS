import respx, httpx

HAPI = "http://hapi:8080/fhir"

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "fhir-bridge"

@respx.mock
def test_patient_event_fhir_disabled(client):
    """When FHIR_ENABLED=false the bridge should accept but skip HAPI push."""
    r = client.post("/api/events/patient-created", json={
        "id": "P001", "mrn": "MRN001", "first_name": "Alice",
        "last_name": "Smith", "birth_date": "1990-01-01", "sex": "F"
    })
    assert r.status_code in (200, 201, 202)

@respx.mock
def test_encounter_admitted_event(client):
    r = client.post("/api/events/encounter-admitted", json={
        "id": 1, "patient_id": "P001", "encounter_type": "inpatient",
        "admit_date": "2026-01-01T09:00:00", "ward": "ICU"
    })
    assert r.status_code in (200, 201, 202)

@respx.mock
def test_order_created_event(client):
    r = client.post("/api/events/imaging-order", json={
        "id": 10, "patient_id": "P001", "order_type": "IMAGING",
        "order_detail": '{"modality": "CT", "body_part": "CHEST"}'
    })
    assert r.status_code in (200, 201, 202)

@respx.mock
def test_report_final_event(client):
    r = client.post("/api/events/report-final", json={
        "report_id": 5, "order_id": 10,
        "impression": "Normal study.", "status": "FINAL"
    })
    assert r.status_code in (200, 201, 202)

@respx.mock
def test_ai_job_completed_event(client):
    r = client.post("/api/events/ai-job-completed", json={
        "job_id": "abc123", "pipeline_id": "poc-xray",
        "patient_id": "P001", "modality": "CR",
        "normal": True, "findings": [], "impression": "No acute findings."
    })
    assert r.status_code in (200, 201, 202)
