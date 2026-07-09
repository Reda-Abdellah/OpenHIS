"""
Tests for services/ai-controller/bus_consumer.py.

All tests run with REDIS_URL='' (set in conftest) so no real Redis connection
is attempted. Redis calls in consume_loop are never reached.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── rule matching ──────────────────────────────────────────────────────────────

def test_matches_clinical_rule_empty_filter_matches_all(fresh_db):
    import bus_consumer
    rule = {"trigger_filter": "{}"}
    assert bus_consumer._matches_clinical_rule(rule, {"oe_id": "123", "subject": "Patient/p1"}) is True


def test_matches_clinical_rule_empty_filter_matches_empty_payload(fresh_db):
    import bus_consumer
    assert bus_consumer._matches_clinical_rule({"trigger_filter": "{}"}, {}) is True


def test_matches_clinical_rule_single_key_match(fresh_db):
    import bus_consumer
    rule = {"trigger_filter": '{"test_code": "CBC"}'}
    assert bus_consumer._matches_clinical_rule(rule, {"test_code": "CBC", "oe_id": "dr-1"}) is True


def test_matches_clinical_rule_single_key_no_match(fresh_db):
    import bus_consumer
    rule = {"trigger_filter": '{"test_code": "CBC"}'}
    assert bus_consumer._matches_clinical_rule(rule, {"test_code": "HBA1C"}) is False


def test_matches_clinical_rule_multi_key_all_match(fresh_db):
    import bus_consumer
    rule = {"trigger_filter": '{"panel": "CBC", "priority": "STAT"}'}
    assert bus_consumer._matches_clinical_rule(
        rule, {"panel": "CBC", "priority": "STAT", "extra": "ignored"}
    ) is True


def test_matches_clinical_rule_multi_key_partial_fail(fresh_db):
    import bus_consumer
    rule = {"trigger_filter": '{"panel": "CBC", "priority": "STAT"}'}
    assert bus_consumer._matches_clinical_rule(
        rule, {"panel": "CBC", "priority": "ROUTINE"}
    ) is False


def test_matches_clinical_rule_malformed_json_returns_false(fresh_db):
    import bus_consumer
    rule = {"trigger_filter": "not-valid-json"}
    assert bus_consumer._matches_clinical_rule(rule, {"oe_id": "x"}) is False


def test_matches_clinical_rule_none_filter_treated_as_empty(fresh_db):
    import bus_consumer
    rule = {"trigger_filter": None}
    assert bus_consumer._matches_clinical_rule(rule, {"anything": "value"}) is True


# ── dedup guard ────────────────────────────────────────────────────────────────

def test_check_existing_clinical_job_no_job_returns_false(fresh_db):
    import bus_consumer
    assert bus_consumer._check_existing_clinical_job("dr-001", "poc-lab-risk") is False


def test_check_existing_clinical_job_detects_pending(fresh_db):
    import bus_consumer
    from database import get_db
    from datetime import datetime, timezone
    import uuid
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,"
            "source_type,event_source_id,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "", "lab_result", "dr-001", "PENDING", "AUTO", now),
        )
    assert bus_consumer._check_existing_clinical_job("dr-001", "poc-lab-risk") is True


def test_check_existing_clinical_job_detects_running(fresh_db):
    import bus_consumer
    from database import get_db
    from datetime import datetime, timezone
    import uuid
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,"
            "source_type,event_source_id,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "", "lab_result", "dr-001", "RUNNING", "AUTO", now),
        )
    assert bus_consumer._check_existing_clinical_job("dr-001", "poc-lab-risk") is True


def test_check_existing_clinical_job_allows_retry_after_failed(fresh_db):
    import bus_consumer
    from database import get_db
    from datetime import datetime, timezone
    import uuid
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,"
            "source_type,event_source_id,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "", "lab_result", "dr-001", "FAILED", "AUTO", now),
        )
    # FAILED job should NOT block a new job
    assert bus_consumer._check_existing_clinical_job("dr-001", "poc-lab-risk") is False


# ── handle_lab_result_ready ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_lab_result_no_oe_id_does_nothing(fresh_db):
    import bus_consumer
    from database import get_db
    await bus_consumer._handle_lab_result_ready({"subject": "Patient/p1"})
    with get_db() as db:
        count = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_handle_lab_result_no_matching_rule_does_nothing(fresh_db):
    """poc-lab-risk rule has auto_trigger=0 by default in seeds — no job created."""
    import bus_consumer
    from database import get_db
    await bus_consumer._handle_lab_result_ready({"oe_id": "dr-001", "subject": "Patient/p1"})
    with get_db() as db:
        count = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_handle_lab_result_creates_job_when_rule_enabled(fresh_db):
    """Enable the auto-trigger on poc-lab-risk rule and verify a job is created."""
    import bus_consumer
    from database import get_db
    # Enable auto_trigger on the seeded poc-lab-risk rule
    with get_db() as db:
        db.execute(
            "UPDATE rules SET auto_trigger=1 WHERE pipeline_id='poc-lab-risk'"
        )
    with patch("asyncio.create_task") as mock_task:
        await bus_consumer._handle_lab_result_ready({"oe_id": "dr-002", "subject": "Patient/p2"})
    with get_db() as db:
        jobs = db.execute(
            "SELECT * FROM jobs WHERE event_source_id='dr-002'"
        ).fetchall()
    assert len(jobs) == 1
    assert jobs[0]["source_type"] == "lab_result"
    assert jobs[0]["status"] == "PENDING"
    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_handle_lab_result_dedup_prevents_second_job(fresh_db):
    """Second identical event does not create a second job."""
    import bus_consumer
    from database import get_db
    with get_db() as db:
        db.execute("UPDATE rules SET auto_trigger=1 WHERE pipeline_id='poc-lab-risk'")
    with patch("asyncio.create_task"):
        await bus_consumer._handle_lab_result_ready({"oe_id": "dr-003", "subject": "Patient/p3"})
        await bus_consumer._handle_lab_result_ready({"oe_id": "dr-003", "subject": "Patient/p3"})
    with get_db() as db:
        count = db.execute(
            "SELECT count(*) FROM jobs WHERE event_source_id='dr-003'"
        ).fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_handle_lab_result_trigger_filter_mismatch_skips(fresh_db):
    """Rule with non-matching trigger_filter does not create a job."""
    import bus_consumer
    from database import get_db
    with get_db() as db:
        db.execute(
            "UPDATE rules SET auto_trigger=1, trigger_filter=? WHERE pipeline_id='poc-lab-risk'",
            ('{"panel": "CBC"}',),
        )
    with patch("asyncio.create_task") as mock_task:
        await bus_consumer._handle_lab_result_ready({"oe_id": "dr-004", "panel": "HBA1C"})
    mock_task.assert_not_called()


# ── handle_patient_synced ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_patient_synced_no_omrs_id_does_nothing(fresh_db):
    import bus_consumer
    from database import get_db
    await bus_consumer._handle_patient_synced({"mrn": "P001", "oe_id": "oe-1"})
    with get_db() as db:
        count = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_handle_patient_synced_creates_emr_job_when_enabled(fresh_db):
    import bus_consumer
    from database import get_db
    with get_db() as db:
        db.execute("UPDATE rules SET auto_trigger=1 WHERE pipeline_id='poc-emr-alert'")
    with patch("asyncio.create_task") as mock_task:
        await bus_consumer._handle_patient_synced({
            "omrs_id": "omrs-001", "oe_id": "oe-001", "mrn": "P001"
        })
    with get_db() as db:
        jobs = db.execute(
            "SELECT * FROM jobs WHERE event_source_id='omrs-001'"
        ).fetchall()
    assert len(jobs) == 1
    assert jobs[0]["source_type"] == "emr_event"
    mock_task.assert_called_once()


# ── consume_loop ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consume_loop_disabled_without_redis_url(fresh_db, monkeypatch):
    import bus_consumer
    monkeypatch.setattr(bus_consumer, "REDIS_URL", "")
    # Should return immediately without error
    await bus_consumer.consume_loop()


# ── SDK dispatch (replaces the old local _process_message) ─────────────────────

@pytest.mark.asyncio
async def test_sdk_dispatch_unknown_event_type_ignored(fresh_db):
    """Unknown event types are ignored by the SDK consumer without raising."""
    import bus_consumer
    from openhis_sdk.bus import BusConsumer

    consumer = BusConsumer(
        redis_url="",
        group=bus_consumer.GROUP,
        consumer=bus_consumer.CONSUMER,
        handlers=bus_consumer._HANDLERS,
    )
    # Should not raise even for unknown event types
    await consumer._process(
        "1-1",
        {"type": "pharmacy.order.sent", "payload": '{"id": "rx-001"}', "source": "odoo"},
    )
