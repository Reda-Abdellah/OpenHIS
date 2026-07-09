"""
DEF-002 / V&V S1.7 — the admin bus consumer bridges patient.synced events
into the admin audit log.

Invokes the handler directly (no Redis needed) and asserts the audit row:
action="patient.synced", actor="system", and the MRN inside `detail` —
exactly what tests/e2e/test_s01_patient_identity.py::test_s1_7 greps for.
"""
import asyncio


def test_patient_synced_event_writes_audit_row(client, auth_headers):
    import bus_consumer

    asyncio.run(bus_consumer.handle_patient_synced(
        {"master_id": "mpi-master-123", "mrn": "MRN-S17-0042"}
    ))

    resp = client.get("/api/audit", headers=auth_headers)
    assert resp.status_code == 200
    rows = [r for r in resp.json() if r.get("action") == "patient.synced"]
    assert rows, "no patient.synced audit row written by the bus handler"
    row = rows[0]
    assert row["admin_user"] == "system"
    assert row["target"] == "mpi-master-123"
    assert "MRN-S17-0042" in (row.get("detail") or "")


def test_patient_synced_handler_tolerates_sparse_payload(client, auth_headers):
    """Events without master_id/mrn must still audit (no crash, no None blowup)."""
    import bus_consumer

    asyncio.run(bus_consumer.handle_patient_synced({}))

    resp = client.get("/api/audit", headers=auth_headers)
    assert resp.status_code == 200
    assert any(r.get("action") == "patient.synced" for r in resp.json())


def test_build_consumer_subscribes_patient_synced(client):
    """The consumer factory wires group=admin and the patient.synced handler."""
    import bus_consumer

    consumer = bus_consumer.build_consumer()
    assert consumer._group == "admin"
    assert consumer._consumer == "admin-1"
    assert consumer._handlers.get("patient.synced") is bus_consumer.handle_patient_synced
