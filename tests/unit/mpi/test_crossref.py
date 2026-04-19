"""
HTTP-level tests for services/mpi/routers/crossref.py.

A cross-reference links a master_patient.id to an external system identifier
(openmrs UUID, openelis lab id, dicom patient id, …). The (system, system_id)
tuple is unique. This is the integration spine that lets every downstream
consumer resolve a foreign id to a single canonical patient.
"""
import uuid
import pytest


def _new_patient(client, **overrides):
    body = {
        "mrn": f"MRN-{uuid.uuid4().hex[:8]}",
        "firstname": "X", "lastname": "Y",
    }
    body.update(overrides)
    r = client.post("/api/patients", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ── POST /api/crossref ────────────────────────────────────────────────────────


def test_create_xref_returns_201_and_persists(client):
    p = _new_patient(client)
    r = client.post("/api/crossref", json={
        "master_id": p["id"], "system": "openmrs", "system_id": "omrs-1",
        "mrn": "MRN-1", "assigning_authority": "OPENMRS",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["master_id"] == p["id"]
    assert body["system"] == "openmrs"
    assert body["system_id"] == "omrs-1"
    assert body["mrn"] == "MRN-1"


def test_create_xref_duplicate_returns_409(client):
    p = _new_patient(client)
    payload = {"master_id": p["id"], "system": "openmrs", "system_id": "omrs-dup"}
    assert client.post("/api/crossref", json=payload).status_code == 201
    r = client.post("/api/crossref", json=payload)
    assert r.status_code == 409


def test_create_xref_for_unknown_master_returns_404(client):
    r = client.post("/api/crossref", json={
        "master_id": str(uuid.uuid4()), "system": "openmrs", "system_id": "x",
    })
    assert r.status_code == 404


def test_create_xref_optional_fields_default_to_null(client):
    p = _new_patient(client)
    r = client.post("/api/crossref", json={
        "master_id": p["id"], "system": "dicom", "system_id": "dcm-1",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["mrn"] is None
    assert body["assigning_authority"] is None


# ── GET /api/crossref ─────────────────────────────────────────────────────────


def test_list_xrefs_empty(client):
    assert client.get("/api/crossref").json() == []


def test_list_xrefs_filter_by_master_id(client):
    p1 = _new_patient(client)
    p2 = _new_patient(client)
    client.post("/api/crossref", json={
        "master_id": p1["id"], "system": "openmrs", "system_id": "p1-omrs",
    })
    client.post("/api/crossref", json={
        "master_id": p2["id"], "system": "openmrs", "system_id": "p2-omrs",
    })
    rows = client.get(f"/api/crossref?master_id={p1['id']}").json()
    assert {r["system_id"] for r in rows} == {"p1-omrs"}


def test_list_xrefs_filter_by_system(client):
    p = _new_patient(client)
    client.post("/api/crossref", json={
        "master_id": p["id"], "system": "openmrs", "system_id": "omrs-x",
    })
    client.post("/api/crossref", json={
        "master_id": p["id"], "system": "openelis", "system_id": "oe-x",
    })
    rows = client.get("/api/crossref?system=openelis").json()
    systems = {r["system"] for r in rows}
    assert systems == {"openelis"}


# ── DELETE /api/crossref/{id} ─────────────────────────────────────────────────


def test_delete_xref_removes_row(client):
    p = _new_patient(client)
    created = client.post("/api/crossref", json={
        "master_id": p["id"], "system": "openmrs", "system_id": "omrs-del",
    }).json()
    r = client.delete(f"/api/crossref/{created['id']}")
    assert r.status_code == 204
    rows = client.get(f"/api/crossref?master_id={p['id']}").json()
    assert rows == []


def test_delete_xref_missing_returns_404(client):
    r = client.delete("/api/crossref/9999999")
    assert r.status_code == 404
