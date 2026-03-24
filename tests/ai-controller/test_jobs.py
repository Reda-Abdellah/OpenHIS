def test_list_jobs_empty(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_list_jobs_with_filter(client):
    r = client.get("/api/jobs?status=completed")
    assert r.status_code == 200

def test_manual_trigger_orthanc_unreachable(client):
    """Trigger should fail gracefully when Orthanc is not available."""
    # poc-xray is seeded by init_db; no need to create it
    r = client.post("/api/jobs", json={
        "pipeline_id": "poc-xray",
        "orthanc_series_id": "FAKE-SERIES-ID",
        "trigger_type": "MANUAL"
    })
    # Expect either 503 (Orthanc unreachable) or 202 (queued)
    assert r.status_code in (202, 503)
