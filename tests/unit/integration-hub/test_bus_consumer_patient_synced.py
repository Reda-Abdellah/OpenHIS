"""Unit tests — hub patient.synced consumer (DEF-010 closure).

Dispatch-level tests with the MPI reads and the OpenELIS adapter
monkeypatched; no Redis, no network.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

HUB_PATH = str(
    Path(__file__).parent.parent.parent.parent / "services" / "integration-hub"
)


def _clear_hub_modules() -> None:
    for mod in list(sys.modules.keys()):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "hub-audit.db"))
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("OPENMRS_URL", "http://openmrs-test:9996")
    monkeypatch.setenv("OPENELIS_URL", "http://openelis-test:9996")
    monkeypatch.setenv("ODOO_URL", "http://odoo-test:9996")
    monkeypatch.setenv("ODOO_DB", "odoo")
    monkeypatch.delenv("REDIS_URL", raising=False)

    if HUB_PATH not in sys.path:
        sys.path.insert(0, HUB_PATH)
    _clear_hub_modules()

    import app.bus_consumer as bc
    yield bc
    _clear_hub_modules()


MASTER = {
    "id": "m-1", "mrn": "MRN-42", "firstname": "Awa", "lastname": "Diallo",
    "sex": "female", "birthdate": "1990-02-01", "status": "active",
}
XREFS = [{"system": "openmrs", "system_id": "omrs-abc"}]


def _silence_audit(monkeypatch, bc):
    calls: list[tuple] = []

    async def rec(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(bc.audit, "log_event", rec)
    return calls


def test_non_mpi_events_are_ignored(consumer, monkeypatch):
    called = []

    async def boom(path):
        called.append(path)

    monkeypatch.setattr(consumer, "_mpi_get", boom)
    asyncio.run(consumer._handle_patient_synced(
        {"omrs_id": "x", "oe_id": "y", "source": "integration-hub"}))
    asyncio.run(consumer._handle_patient_synced({"mrn": "M1"}))  # no source
    assert called == []


def test_mpi_event_upserts_into_openelis(consumer, monkeypatch):
    audits = _silence_audit(monkeypatch, consumer)
    upserted: list[dict] = []

    async def fake_mpi_get(path):
        if path == "/api/patients/m-1":
            return dict(MASTER)
        if path.startswith("/api/crossref"):
            return list(XREFS)
        raise AssertionError(f"unexpected MPI path {path}")

    async def fake_upsert(patient):
        upserted.append(patient)
        return "oe-9"

    monkeypatch.setattr(consumer, "_mpi_get", fake_mpi_get)
    monkeypatch.setattr(consumer.openelis, "upsert_patient", fake_upsert)

    asyncio.run(consumer._handle_patient_synced(
        {"master_id": "m-1", "mrn": "MRN-42", "source": "mpi"}))

    assert len(upserted) == 1
    patient = upserted[0]
    assert patient["resourceType"] == "Patient"
    idents = {(i.get("system"), i["value"]) for i in patient["identifier"]}
    assert (consumer.MRN_SYSTEM, "MRN-42") in idents
    assert ("urn:openhis:openmrs", "omrs-abc") in idents
    assert patient["gender"] == "female"
    assert patient["birthDate"] == "1990-02-01"
    assert any(a[0] == "patient_synced" and a[3] == "mpi→oe" for a in audits)


def test_mpi_unavailable_raises_for_redelivery(consumer, monkeypatch):
    async def fake_mpi_get(path):
        return None

    monkeypatch.setattr(consumer, "_mpi_get", fake_mpi_get)
    with pytest.raises(RuntimeError):
        asyncio.run(consumer._handle_patient_synced(
            {"master_id": "m-1", "source": "mpi"}))


def test_openelis_failure_raises_after_audit(consumer, monkeypatch):
    audits = _silence_audit(monkeypatch, consumer)

    async def fake_mpi_get(path):
        return dict(MASTER) if "patients" in path else []

    async def fake_upsert(patient):
        return None

    monkeypatch.setattr(consumer, "_mpi_get", fake_mpi_get)
    monkeypatch.setattr(consumer.openelis, "upsert_patient", fake_upsert)

    with pytest.raises(RuntimeError):
        asyncio.run(consumer._handle_patient_synced(
            {"master_id": "m-1", "source": "mpi"}))
    assert any(a[0] == "patient_sync_failed" for a in audits)
