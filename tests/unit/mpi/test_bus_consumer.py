"""
Tests for services/mpi/bus_consumer.py.

Bus topology under test (from openhis.service.json + bus_consumer.py):

    integration-hub  ──patient.registered──>  MPI bus_consumer
                                                │
                            upsert master_patients
                            upsert cross_references (openmrs, openelis)
                            insert audit_log row
                                                │
                                                ▼
                                       publish patient.synced

All tests run with REDIS_URL='' (set in conftest) so no real Redis connection is
attempted; publish_event is patched on a per-test basis.
"""
import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest


# ── helpers ────────────────────────────────────────────────────────────────────

def _seed_master_patient(mrn: str, **fields) -> str:
    from database import get_db
    pid = str(uuid.uuid4())
    cols = {"id": pid, "mrn": mrn, "firstname": "Test", "lastname": "Patient",
            "status": "active"}
    cols.update(fields)
    placeholders = ",".join(["?"] * len(cols))
    columns = ",".join(cols.keys())
    with get_db() as db:
        db.execute(
            f"INSERT INTO master_patients ({columns}) VALUES ({placeholders})",
            tuple(cols.values()),
        )
    return pid


# ── _handle_patient_registered ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_mrn_skips_silently():
    import bus_consumer
    from database import get_db
    with patch("bus_consumer.publish_event", new=AsyncMock()) as mock_pub:
        await bus_consumer._handle_patient_registered(
            {"omrs_id": "omrs-1", "oe_id": "oe-1"}
        )
    with get_db() as db:
        n_xref = db.execute("SELECT COUNT(*) AS n FROM cross_references").fetchone()["n"]
        n_mp = db.execute("SELECT COUNT(*) AS n FROM master_patients").fetchone()["n"]
    assert n_xref == 0
    assert n_mp == 0
    mock_pub.assert_not_awaited()


@pytest.mark.asyncio
async def test_blank_mrn_skips_silently():
    import bus_consumer
    from database import get_db
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered({"mrn": "   "})
    with get_db() as db:
        n_mp = db.execute("SELECT COUNT(*) AS n FROM master_patients").fetchone()["n"]
    assert n_mp == 0


@pytest.mark.asyncio
async def test_unknown_mrn_creates_master_record():
    """
    Per current contract, an event for an unknown MRN creates a new master
    patient (does not silently drop). The original test asserted the opposite,
    but it referenced a `_handle_patient_synced` function that no longer exists;
    the live `_handle_patient_registered` is canonical and creates on miss.
    """
    import bus_consumer
    from database import get_db
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered({
            "mrn": "NEW-MRN-001", "omrs_id": "omrs-x",
            "firstname": "Alice", "lastname": "Smith",
        })
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM master_patients WHERE mrn=?", ("NEW-MRN-001",)
        ).fetchone()
    assert row is not None
    assert row["firstname"] == "Alice"
    assert row["lastname"] == "Smith"


@pytest.mark.asyncio
async def test_inserts_openmrs_crossref_for_existing_master():
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN001")
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered(
            {"omrs_id": "omrs-uuid-001", "mrn": "MRN001"}
        )
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM cross_references "
            "WHERE system='openmrs' AND system_id='omrs-uuid-001'"
        ).fetchone()
    assert row is not None
    assert row["mrn"] == "MRN001"


@pytest.mark.asyncio
async def test_inserts_both_crossrefs_when_both_ids_present():
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN002")
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered(
            {"omrs_id": "omrs-002", "oe_id": "oe-002", "mrn": "MRN002"}
        )
    with get_db() as db:
        rows = db.execute(
            "SELECT system FROM cross_references WHERE mrn=?", ("MRN002",)
        ).fetchall()
    systems = {r["system"] for r in rows}
    assert systems == {"openmrs", "openelis"}


@pytest.mark.asyncio
async def test_only_openmrs_xref_when_no_oe_id():
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN004")
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered(
            {"omrs_id": "omrs-004", "mrn": "MRN004"}
        )
    with get_db() as db:
        rows = db.execute(
            "SELECT system FROM cross_references WHERE mrn=?", ("MRN004",)
        ).fetchall()
    assert [r["system"] for r in rows] == ["openmrs"]


@pytest.mark.asyncio
async def test_idempotent_on_repeat_call():
    """Replaying the same event must not duplicate cross-references."""
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN003")
    payload = {"omrs_id": "omrs-003", "oe_id": "oe-003", "mrn": "MRN003"}
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered(payload)
        await bus_consumer._handle_patient_registered(payload)
    with get_db() as db:
        n = db.execute(
            "SELECT COUNT(*) AS n FROM cross_references WHERE mrn=?", ("MRN003",)
        ).fetchone()["n"]
    assert n == 2  # one openmrs + one openelis, no duplicates


@pytest.mark.asyncio
async def test_replay_with_changed_mrn_updates_xref_mrn():
    """
    The ON CONFLICT clause re-asserts the latest MRN on the cross-reference
    row, so a replay with a corrected MRN should update — not duplicate.
    """
    import bus_consumer
    from database import get_db
    _seed_master_patient("MRN-OLD")
    _seed_master_patient("MRN-NEW")
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered(
            {"omrs_id": "omrs-rekey", "mrn": "MRN-OLD"}
        )
        await bus_consumer._handle_patient_registered(
            {"omrs_id": "omrs-rekey", "mrn": "MRN-NEW"}
        )
    with get_db() as db:
        rows = db.execute(
            "SELECT mrn FROM cross_references WHERE system_id='omrs-rekey'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["mrn"] == "MRN-NEW"


@pytest.mark.asyncio
async def test_demographic_gap_filling_only_overwrites_blanks():
    """
    Per bus_consumer contract, demographic updates from the bus are
    non-destructive — only fill empty fields, never overwrite a populated one.
    """
    import bus_consumer
    from database import get_db
    pid = _seed_master_patient(
        "MRN-DEMO", firstname="Existing", lastname="", birthdate=None, sex=None
    )
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered({
            "mrn": "MRN-DEMO",
            "firstname": "SHOULD-NOT-OVERWRITE",
            "lastname":  "Filled",
            "birthdate": "1980-01-01",
            "sex":       "M",
        })
    with get_db() as db:
        row = db.execute(
            "SELECT firstname, lastname, birthdate, sex FROM master_patients WHERE id=?",
            (pid,),
        ).fetchone()
    assert row["firstname"] == "Existing"        # preserved
    assert row["lastname"] == "Filled"           # filled (was blank)
    assert row["birthdate"] == "1980-01-01"      # filled
    assert row["sex"] == "M"                     # filled


@pytest.mark.asyncio
async def test_publishes_patient_synced_after_upsert():
    """The handler is the canonical producer of patient.synced."""
    import bus_consumer
    _seed_master_patient("MRN-SYNC")
    mock_pub = AsyncMock()
    with patch("bus_consumer.publish_event", new=mock_pub):
        await bus_consumer._handle_patient_registered({
            "mrn": "MRN-SYNC", "omrs_id": "omrs-sync", "oe_id": "oe-sync",
        })
    mock_pub.assert_awaited_once()
    args, _ = mock_pub.call_args
    # publish_event(client, event_type, payload)
    assert args[1] == "patient.synced"
    payload = args[2]
    assert payload["mrn"] == "MRN-SYNC"
    assert payload["omrs_id"] == "omrs-sync"
    assert payload["oe_id"] == "oe-sync"
    assert payload["master_id"]  # populated UUID, not empty


@pytest.mark.asyncio
async def test_audit_row_recorded_for_new_master():
    import bus_consumer
    from database import get_db
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        await bus_consumer._handle_patient_registered({
            "mrn": "MRN-AUD", "omrs_id": "omrs-aud",
            "firstname": "A", "lastname": "B",
        })
    with get_db() as db:
        rows = db.execute(
            "SELECT action, details FROM audit_log "
            "WHERE master_id=(SELECT id FROM master_patients WHERE mrn=?)",
            ("MRN-AUD",),
        ).fetchall()
    actions = [r["action"] for r in rows]
    assert "created-from-bus" in actions


# ── consume_loop disabled when REDIS_URL empty ────────────────────────────────


@pytest.mark.asyncio
async def test_consume_loop_returns_immediately_without_redis_url(monkeypatch):
    """
    The SDK BusConsumer.run() returns immediately when redis_url is empty.
    consume_loop() wraps it, so the same guard applies and the task completes
    without raising.
    """
    import bus_consumer
    monkeypatch.setattr(bus_consumer, "REDIS_URL", "")
    # Should complete within a short timeout — no blocking on Redis I/O
    await asyncio.wait_for(bus_consumer.consume_loop(), timeout=2.0)


# ── End-to-end dispatch via SDK BusConsumer._process ──────────────────────────


@pytest.mark.asyncio
async def test_sdk_consumer_dispatches_patient_registered_to_handler():
    """
    Verifies the SDK-level dispatch path (BusConsumer._process) routes a
    patient.registered stream entry to MPI's handler with the deserialised
    payload. This replaces the obsolete _process_message tests.
    """
    import bus_consumer
    from database import get_db
    from openhis_sdk.bus import BusConsumer

    _seed_master_patient("MRN-SDK")
    with patch("bus_consumer.publish_event", new=AsyncMock()):
        consumer = BusConsumer(
            redis_url="",
            group="mpi",
            consumer="mpi-test",
            handlers={"patient.registered": bus_consumer._handle_patient_registered},
        )
        await consumer._process(
            "1-1",
            {
                "type": "patient.registered",
                "payload": json.dumps({"mrn": "MRN-SDK", "omrs_id": "omrs-sdk"}),
            },
        )

    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM cross_references WHERE system='openmrs' AND system_id='omrs-sdk'"
        ).fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_sdk_consumer_ignores_unknown_event_types():
    from openhis_sdk.bus import BusConsumer

    handler = AsyncMock()
    consumer = BusConsumer(
        redis_url="",
        group="mpi",
        consumer="mpi-test",
        handlers={"patient.registered": handler},
    )
    await consumer._process(
        "1-1",
        {"type": "lab.result.ready", "payload": json.dumps({"oe_id": "x"})},
    )
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdk_consumer_swallows_handler_exceptions():
    """A failing handler must not crash the consumer (logged-and-continue)."""
    from openhis_sdk.bus import BusConsumer

    async def raises(_payload):
        raise RuntimeError("boom")

    consumer = BusConsumer(
        redis_url="",
        group="mpi",
        consumer="mpi-test",
        handlers={"patient.registered": raises},
    )
    # Should not raise
    await consumer._process(
        "1-1",
        {"type": "patient.registered", "payload": "{}"},
    )
