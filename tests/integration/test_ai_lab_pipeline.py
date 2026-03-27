"""
Integration tests: lab result bus event → AI pipeline job creation.

Verifies the full flow without real Redis:
  1. Register a lab_result pipeline + auto-trigger rule via the API.
  2. Directly invoke bus_consumer._handle_lab_result_ready() (simulates Redis delivery).
  3. Assert a PENDING job is created with correct source_type and event_source_id.
"""
import asyncio
import pytest
from unittest.mock import patch


# ── helpers ────────────────────────────────────────────────────────────────────

def _create_lab_pipeline(ai_client, pipeline_id: str = "test-lab-risk"):
    r = ai_client.post("/api/pipelines", json={
        "id": pipeline_id,
        "name": "Test Lab Risk",
        "docker_image": "test/lab-risk:latest",
        "source_type": "lab_result",
    })
    assert r.status_code == 201, r.text
    return r.json()


def _create_auto_trigger_rule(ai_client, pipeline_id: str, trigger_filter: str = "{}"):
    r = ai_client.post("/api/rules", json={
        "pipeline_id": pipeline_id,
        "name": "Lab auto-trigger",
        "trigger_filter": trigger_filter,
        "auto_trigger": 1,
        "enabled": 1,
    })
    assert r.status_code == 201, r.text
    return r.json()


# ── tests ──────────────────────────────────────────────────────────────────────

def test_lab_pipeline_seeds_job_on_event(ai_client):
    """bus event with matching rule creates a PENDING lab_result job."""
    _create_lab_pipeline(ai_client)
    _create_auto_trigger_rule(ai_client, "test-lab-risk")

    import bus_consumer
    with patch("asyncio.create_task"):
        asyncio.get_event_loop().run_until_complete(
            bus_consumer._handle_lab_result_ready({
                "oe_id": "dr-integration-001",
                "subject": "Patient/omrs-integration-001",
            })
        )

    r = ai_client.get("/api/jobs")
    assert r.status_code == 200
    jobs = r.json()
    matching = [j for j in jobs if j.get("event_source_id") == "dr-integration-001"]
    assert len(matching) == 1
    assert matching[0]["source_type"] == "lab_result"
    assert matching[0]["status"] == "PENDING"


def test_lab_pipeline_no_duplicate_job(ai_client):
    """Second identical event does not create a second job."""
    _create_lab_pipeline(ai_client, "test-lab-dedup")
    _create_auto_trigger_rule(ai_client, "test-lab-dedup")

    import bus_consumer
    payload = {"oe_id": "dr-dedup-001", "subject": "Patient/p1"}
    with patch("asyncio.create_task"):
        asyncio.get_event_loop().run_until_complete(
            bus_consumer._handle_lab_result_ready(payload)
        )
        asyncio.get_event_loop().run_until_complete(
            bus_consumer._handle_lab_result_ready(payload)
        )

    r = ai_client.get("/api/jobs")
    matching = [j for j in r.json() if j.get("event_source_id") == "dr-dedup-001"]
    assert len(matching) == 1


def test_source_type_visible_in_job_list(ai_client):
    """Jobs created by clinical events expose source_type in the list endpoint."""
    _create_lab_pipeline(ai_client, "test-lab-st")
    _create_auto_trigger_rule(ai_client, "test-lab-st")

    import bus_consumer
    with patch("asyncio.create_task"):
        asyncio.get_event_loop().run_until_complete(
            bus_consumer._handle_lab_result_ready({
                "oe_id": "dr-st-001", "subject": "Patient/p-st"
            })
        )

    r = ai_client.get("/api/jobs?source_type=lab_result")
    assert r.status_code == 200
    jobs = r.json()
    assert all(j["source_type"] == "lab_result" for j in jobs)
    assert any(j["event_source_id"] == "dr-st-001" for j in jobs)


def test_filter_mismatch_no_job_created(ai_client):
    """Rule with non-matching trigger_filter blocks job creation."""
    _create_lab_pipeline(ai_client, "test-lab-filter")
    _create_auto_trigger_rule(ai_client, "test-lab-filter",
                              trigger_filter='{"panel": "CBC"}')

    import bus_consumer
    with patch("asyncio.create_task") as mock_task:
        asyncio.get_event_loop().run_until_complete(
            bus_consumer._handle_lab_result_ready({
                "oe_id": "dr-filter-001", "panel": "HBA1C"
            })
        )

    mock_task.assert_not_called()
    r = ai_client.get("/api/jobs")
    matching = [j for j in r.json() if j.get("event_source_id") == "dr-filter-001"]
    assert len(matching) == 0


def test_manual_clinical_job_via_api(ai_client):
    """Clinical job can also be created manually via POST /api/jobs."""
    _create_lab_pipeline(ai_client, "test-lab-manual")

    with patch("runner.run_job"):   # prevent actual runner execution
        r = ai_client.post("/api/jobs", json={
            "pipeline_id": "test-lab-manual",
            "event_source_id": "dr-manual-001",
            "event_payload": {"oe_id": "dr-manual-001", "subject": "Patient/p-manual"},
        })

    assert r.status_code == 202, r.text
    data = r.json()
    assert data["source_type"] == "lab_result"
    assert data["status"] == "PENDING"
