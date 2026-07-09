"""
DEF-010 — MPI must publish patient.synced for REST-driven patient mutations.

Before this fix, only the bus-consumer path (patient.registered → upsert →
publish_event) emitted patient.synced; patients created/updated/merged via
the REST API were invisible to downstream consumers (admin audit bridge,
analytics), so V&V S1.7 (admin audit row action='patient.synced' with the
MRN in detail) could never pass for REST-created patients.

Coverage:

  Route-level (DB-bound, skipped when PostgreSQL is unavailable):
    - POST /api/patients          → publishes {master_id, mrn, source:'mpi'}
    - PATCH /api/patients/{id}    → publishes for the updated record
    - POST /api/patients/{id}/merge → publishes for the SURVIVING record
    - 409 MRN conflict            → does NOT publish (rollback ≠ event)
    - bus.publish raising         → request still succeeds (201)
    - REDIS_URL unset (real path) → request still succeeds (201)

  Helper-level (no DB needed, pytest.mark.no_db):
    - publish() returns None when REDIS_URL is unset
    - publish() never raises when Redis is unreachable
"""
import sys
import uuid
from pathlib import Path

import pytest

from .conftest import requires_pg

MPI_PATH = str(Path(__file__).parent.parent.parent.parent / "services" / "mpi")


# ── helpers ───────────────────────────────────────────────────────────────────


def _create(client, **overrides):
    body = {
        "mrn": f"MRN-{uuid.uuid4().hex[:8]}",
        "firstname": "Alice",
        "lastname": "Doe",
        "birthdate": "1980-01-01",
        "sex": "F",
    }
    body.update(overrides)
    r = client.post("/api/patients", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def published(client, monkeypatch):
    """Record (event_type, payload) for every bus.publish call from the routes.

    `client` (→ fresh_db) has already re-imported the MPI module chain, so the
    `bus` module imported here is the same object routers.patients references.
    """
    import bus

    calls: list[tuple[str, dict]] = []

    def _record(event_type: str, payload: dict, source: str = "mpi") -> str:
        calls.append((event_type, payload))
        return "0-1"

    monkeypatch.setattr(bus, "publish", _record)
    return calls


def _import_bus_module():
    """Fresh import of services/mpi/bus.py for no_db helper tests."""
    if MPI_PATH in sys.path:
        sys.path.remove(MPI_PATH)
    sys.path.insert(0, MPI_PATH)
    sys.modules.pop("bus", None)
    import bus
    return bus


# ── route-level publication (DB-bound) ───────────────────────────────────────


@requires_pg
def test_create_patient_publishes_patient_synced(client, published):
    p = _create(client, mrn="MRN-PUB-CREATE")
    assert published == [
        ("patient.synced",
         {"master_id": p["id"], "mrn": "MRN-PUB-CREATE", "source": "mpi"}),
    ]


@requires_pg
def test_update_patient_publishes_patient_synced(client, published):
    p = _create(client, mrn="MRN-PUB-PATCH")
    published.clear()  # drop the create event
    r = client.patch(f"/api/patients/{p['id']}", json={"firstname": "New"})
    assert r.status_code == 200
    assert published == [
        ("patient.synced",
         {"master_id": p["id"], "mrn": "MRN-PUB-PATCH", "source": "mpi"}),
    ]


@requires_pg
def test_merge_publishes_patient_synced_for_survivor(client, published):
    survivor = _create(client, mrn="MRN-PUB-SURV")
    duplicate = _create(client, mrn="MRN-PUB-DUP")
    published.clear()  # drop the two create events
    r = client.post(f"/api/patients/{survivor['id']}/merge",
                    json={"merge_id": duplicate["id"]})
    assert r.status_code == 200
    assert published == [
        ("patient.synced",
         {"master_id": survivor["id"], "mrn": "MRN-PUB-SURV", "source": "mpi"}),
    ]


@requires_pg
def test_duplicate_mrn_conflict_does_not_publish(client, published):
    _create(client, mrn="MRN-PUB-409")
    published.clear()
    r = client.post("/api/patients", json={
        "mrn": "MRN-PUB-409", "firstname": "X", "lastname": "Y",
    })
    assert r.status_code == 409
    assert published == []


@requires_pg
def test_create_succeeds_when_publish_raises(client, monkeypatch):
    """A bus failure must NEVER fail the API request."""
    import bus

    def _boom(event_type: str, payload: dict, source: str = "mpi") -> str:
        raise RuntimeError("redis exploded")

    monkeypatch.setattr(bus, "publish", _boom)
    r = client.post("/api/patients", json={
        "mrn": "MRN-PUB-BOOM", "firstname": "A", "lastname": "B",
    })
    assert r.status_code == 201
    # And the row really was committed before the publish attempt.
    assert client.get("/api/patients/lookup?mrn=MRN-PUB-BOOM").status_code == 200


@requires_pg
def test_create_succeeds_without_redis_url(client):
    """Real publish path with REDIS_URL='' (conftest default) — no crash."""
    r = client.post("/api/patients", json={
        "mrn": "MRN-PUB-NOREDIS", "firstname": "A", "lastname": "B",
    })
    assert r.status_code == 201


# ── helper-level failure contract (no DB) ─────────────────────────────────────


@pytest.mark.no_db
def test_publish_returns_none_when_redis_url_unset(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    bus = _import_bus_module()
    assert bus.publish(
        "patient.synced", {"master_id": "m1", "mrn": "X", "source": "mpi"}
    ) is None


@pytest.mark.no_db
def test_publish_never_raises_when_redis_unreachable(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    bus = _import_bus_module()
    assert bus.publish(
        "patient.synced", {"master_id": "m1", "mrn": "X", "source": "mpi"}
    ) is None
