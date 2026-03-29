"""
Tests for services/mpi/bus_consumer.py.

All tests run with REDIS_URL='' (set in conftest) so no real Redis connection
is attempted.
"""
import asyncio
import pytest
from unittest.mock import patch


# ── helpers ────────────────────────────────────────────────────────────────────

def _seed_master_patient(mrn: str) -> str:
    """Insert a master patient and return its ID."""
    import uuid
    from database import get_db
    pid = str(uuid.uuid4())
    with get_db() as db:
        db.execute(
            "INSERT INTO master_patients (id,mrn,firstname,lastname,status)"
            " VALUES (?,?,?,?,?)",
            (pid, mrn, "Test", "Patient", "active"),
        )
    return pid


# ── consume_loop ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consume_loop_disabled_without_redis_url(fresh_db, monkeypatch):
    import bus_consumer
    monkeypatch.setattr(bus_consumer, "REDIS_URL", "")
    await bus_consumer.consume_loop()   # should return immediately without error


# ── _handle_patient_synced ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_patient_synced_no_mrn_skips(fresh_db):
    import bus_consumer
    from database import get_db
    await bus_consumer._handle_patient_synced({"omrs_id": "omrs-1", "oe_id": "oe-1"})
    with get_db() as db:
        count = db.execute("SELECT count(*) FROM cross_references").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_handle_patient_synced_no_master_record_skips(fresh_db):
    """MRN not in master_patients → silently skip, no cross-reference inserted."""
    import bus_consumer
    from database import get_db
    await bus_consumer._handle_patient_synced(
        {"omrs_id": "omrs-1", "oe_id": "oe-1", "mrn": "UNKNOWN-MRN"}
    )
    with get_db() as db:
        count = db.execute("SELECT count(*) FROM cross_references").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_handle_patient_synced_inserts_openmrs_crossref(fresh_db):
    """Valid event with mrn matching a master patient → openmrs cross-ref inserted."""
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN001")
    await bus_consumer._handle_patient_synced(
        {"omrs_id": "omrs-uuid-001", "mrn": "MRN001"}
    )
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM cross_references WHERE system='openmrs' AND system_id='omrs-uuid-001'"
        ).fetchone()
    assert row is not None
    assert row["mrn"] == "MRN001"


@pytest.mark.asyncio
async def test_handle_patient_synced_inserts_both_crossrefs(fresh_db):
    """Both omrs_id and oe_id present → two cross-references created."""
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN002")
    await bus_consumer._handle_patient_synced(
        {"omrs_id": "omrs-002", "oe_id": "oe-002", "mrn": "MRN002"}
    )
    with get_db() as db:
        rows = db.execute(
            "SELECT system FROM cross_references WHERE mrn='MRN002'"
        ).fetchall()
    systems = {r["system"] for r in rows}
    assert "openmrs" in systems
    assert "openelis" in systems


@pytest.mark.asyncio
async def test_handle_patient_synced_idempotent(fresh_db):
    """Second call with same IDs does not raise or create duplicate rows."""
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN003")
    payload = {"omrs_id": "omrs-003", "oe_id": "oe-003", "mrn": "MRN003"}
    await bus_consumer._handle_patient_synced(payload)
    await bus_consumer._handle_patient_synced(payload)  # idempotent upsert
    with get_db() as db:
        count = db.execute(
            "SELECT count(*) FROM cross_references WHERE mrn='MRN003'"
        ).fetchone()[0]
    assert count == 2   # one per system, no duplicates


@pytest.mark.asyncio
async def test_handle_patient_synced_only_omrs_id(fresh_db):
    """If only omrs_id is present (no oe_id), only openmrs crossref is created."""
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN004")
    await bus_consumer._handle_patient_synced(
        {"omrs_id": "omrs-004", "mrn": "MRN004"}
    )
    with get_db() as db:
        rows = db.execute(
            "SELECT system FROM cross_references WHERE mrn='MRN004'"
        ).fetchall()
    systems = [r["system"] for r in rows]
    assert systems == ["openmrs"]


# ── _process_message ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_message_unknown_event_ignored(fresh_db):
    """Unknown event types are silently ignored."""
    import bus_consumer
    await bus_consumer._process_message(
        "1-1",
        {"type": "lab_result.ready", "payload": '{"oe_id": "dr-1"}', "source": "hub"},
    )
    # No error and no DB writes


@pytest.mark.asyncio
async def test_process_message_dispatches_patient_synced(fresh_db):
    """patient.synced event is dispatched to the handler."""
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN005")
    import json
    await bus_consumer._process_message(
        "2-1",
        {
            "type": "patient.synced",
            "payload": json.dumps({"omrs_id": "omrs-005", "mrn": "MRN005"}),
            "source": "integration-hub",
        },
    )
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM cross_references WHERE system='openmrs' AND system_id='omrs-005'"
        ).fetchone()
    assert row is not None
