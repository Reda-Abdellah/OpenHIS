"""
HTTP-level tests for services/mpi/routers/patients.py.

Coverage matrix (one test per feature claim):

  POST /api/patients
    - 201 + body on happy path
    - 409 on MRN conflict
    - audit_log row "created" inserted

  GET /api/patients
    - filter by status (default 'active' excludes merged)
    - filter by free-text q (firstname/lastname/mrn LIKE)
    - empty list when no rows

  GET /api/patients/lookup
    - by mrn
    - by system+system_id (cross-reference)
    - by name+birthdate
    - 404 when nothing matches

  GET /api/patients/{id}
    - returns patient with cross_references and audit included
    - 404 when missing

  PATCH /api/patients/{id}
    - 200 + only the supplied fields are updated; others preserved
    - 400 when no fields supplied
    - 404 when missing
    - 409 when patient is merged
    - audit_log row "updated" inserted

  POST /api/patients/{id}/merge
    - transfers cross-references to the surviving record
    - drops xrefs that would conflict with surviving xrefs
    - marks the merged record as status='merged' with merged_into=<survivor>
    - resolves any pending match_candidates between the pair to confirmed_match
    - 400 when merge_id missing or equals pid
    - 404 when either record is missing or not active
    - audit_log row "merged" inserted

DEV_MODE=true (set in tests/conftest.py) bypasses JWT, so we don't repeat
auth-enforcement tests here — those belong in integration-against-Keycloak.
"""
import uuid
import pytest


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


# ── POST /api/patients ────────────────────────────────────────────────────────


def test_create_patient_returns_201_and_persisted_row(client):
    r = client.post("/api/patients", json={
        "mrn": "MRN-CREATE-1",
        "firstname": "Alice", "lastname": "Doe",
        "birthdate": "1980-01-01", "sex": "F",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["mrn"] == "MRN-CREATE-1"
    assert body["firstname"] == "Alice"
    assert body["status"] == "active"
    assert body["id"]


def test_create_patient_duplicate_mrn_returns_409(client):
    _create(client, mrn="MRN-DUP")
    r = client.post("/api/patients", json={
        "mrn": "MRN-DUP", "firstname": "X", "lastname": "Y",
    })
    assert r.status_code == 409


def test_create_patient_writes_audit_row(client):
    p = _create(client, mrn="MRN-AUDIT-CREATE")
    audit = client.get(f"/api/audit?master_id={p['id']}").json()
    actions = [row["action"] for row in audit]
    assert "created" in actions


# ── GET /api/patients (list) ──────────────────────────────────────────────────


def test_list_patients_empty(client):
    assert client.get("/api/patients").json() == []


def test_list_patients_default_status_excludes_merged(client, db):
    p1 = _create(client, mrn="LIST-1", lastname="Aaa")
    p2 = _create(client, mrn="LIST-2", lastname="Bbb")
    # Mark p2 as merged directly to test the filter
    with db() as conn:
        conn.execute(
            "UPDATE master_patients SET status='merged', merged_into=? WHERE id=?",
            (p1["id"], p2["id"]),
        )
    rows = client.get("/api/patients").json()
    ids = [r["id"] for r in rows]
    assert p1["id"] in ids
    assert p2["id"] not in ids


def test_list_patients_q_filters_by_name_or_mrn(client):
    _create(client, mrn="QFIND-1", firstname="Zelda",  lastname="Nintendo")
    _create(client, mrn="QFIND-2", firstname="Mario",  lastname="Nintendo")
    _create(client, mrn="OTHER-3", firstname="Sonic",  lastname="Sega")

    by_first = client.get("/api/patients?q=Zelda").json()
    assert {r["mrn"] for r in by_first} == {"QFIND-1"}

    by_last = client.get("/api/patients?q=Nintendo").json()
    assert {r["mrn"] for r in by_last} == {"QFIND-1", "QFIND-2"}

    by_mrn = client.get("/api/patients?q=OTHER").json()
    assert {r["mrn"] for r in by_mrn} == {"OTHER-3"}


# ── GET /api/patients/lookup ──────────────────────────────────────────────────


def test_lookup_by_mrn(client):
    p = _create(client, mrn="LOOK-MRN-1")
    r = client.get("/api/patients/lookup?mrn=LOOK-MRN-1")
    assert r.status_code == 200
    assert r.json()["id"] == p["id"]


def test_lookup_by_xref(client):
    p = _create(client, mrn="LOOK-XREF-1")
    client.post("/api/crossref", json={
        "master_id": p["id"], "system": "openmrs", "system_id": "omrs-look-1",
    })
    r = client.get("/api/patients/lookup?system=openmrs&system_id=omrs-look-1")
    assert r.status_code == 200
    assert r.json()["id"] == p["id"]


def test_lookup_by_name_and_birthdate(client):
    p = _create(client, mrn="LOOK-NB-1", firstname="Olivia",
                lastname="Brennan", birthdate="1975-03-22")
    r = client.get(
        "/api/patients/lookup"
        "?firstname=Olivia&lastname=Brennan&birthdate=1975-03-22"
    )
    assert r.status_code == 200
    assert r.json()["id"] == p["id"]


def test_lookup_returns_404_when_no_match(client):
    r = client.get("/api/patients/lookup?mrn=DOES-NOT-EXIST")
    assert r.status_code == 404


def test_lookup_excludes_merged_for_mrn(client, db):
    """An archived (merged) record must not be returned by an MRN lookup."""
    p = _create(client, mrn="LOOK-MERGED")
    with db() as conn:
        conn.execute(
            "UPDATE master_patients SET status='merged' WHERE id=?", (p["id"],)
        )
    r = client.get("/api/patients/lookup?mrn=LOOK-MERGED")
    assert r.status_code == 404


# ── GET /api/patients/{id} ────────────────────────────────────────────────────


def test_get_patient_includes_xrefs_and_audit(client):
    p = _create(client, mrn="GET-1")
    client.post("/api/crossref", json={
        "master_id": p["id"], "system": "openmrs", "system_id": "omrs-get-1",
    })
    r = client.get(f"/api/patients/{p['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == p["id"]
    assert body["cross_references"]
    assert body["cross_references"][0]["system"] == "openmrs"
    assert any(a["action"] == "created" for a in body["audit"])


def test_get_patient_missing_returns_404(client):
    r = client.get(f"/api/patients/{uuid.uuid4()}")
    assert r.status_code == 404


# ── PATCH /api/patients/{id} ──────────────────────────────────────────────────


def test_update_patient_only_supplied_fields_change(client):
    p = _create(client, mrn="PATCH-1", firstname="Old", lastname="Name")
    r = client.patch(f"/api/patients/{p['id']}", json={"firstname": "New"})
    assert r.status_code == 200
    body = r.json()
    assert body["firstname"] == "New"
    assert body["lastname"] == "Name"  # untouched


def test_update_patient_no_fields_returns_400(client):
    p = _create(client, mrn="PATCH-EMPTY")
    r = client.patch(f"/api/patients/{p['id']}", json={})
    assert r.status_code == 400


def test_update_patient_missing_returns_404(client):
    r = client.patch(f"/api/patients/{uuid.uuid4()}", json={"firstname": "X"})
    assert r.status_code == 404


def test_update_patient_merged_returns_409(client, db):
    p = _create(client, mrn="PATCH-MERGED")
    with db() as conn:
        conn.execute(
            "UPDATE master_patients SET status='merged' WHERE id=?", (p["id"],)
        )
    r = client.patch(f"/api/patients/{p['id']}", json={"firstname": "X"})
    assert r.status_code == 409


def test_update_patient_writes_audit_row(client):
    p = _create(client, mrn="PATCH-AUDIT")
    client.patch(f"/api/patients/{p['id']}", json={"firstname": "Z"})
    audit = client.get(f"/api/audit?master_id={p['id']}").json()
    assert any(row["action"] == "updated" for row in audit)


# ── POST /api/patients/{id}/merge ─────────────────────────────────────────────


def test_merge_transfers_crossrefs_to_survivor(client):
    survivor = _create(client, mrn="MERGE-SURV")
    duplicate = _create(client, mrn="MERGE-DUP")

    client.post("/api/crossref", json={
        "master_id": duplicate["id"], "system": "openmrs", "system_id": "omrs-dup",
    })
    client.post("/api/crossref", json={
        "master_id": duplicate["id"], "system": "openelis", "system_id": "oe-dup",
    })

    r = client.post(f"/api/patients/{survivor['id']}/merge",
                    json={"merge_id": duplicate["id"]})
    assert r.status_code == 200

    xrefs = client.get(f"/api/crossref?master_id={survivor['id']}").json()
    pairs = {(x["system"], x["system_id"]) for x in xrefs}
    assert pairs == {("openmrs", "omrs-dup"), ("openelis", "oe-dup")}


def test_merge_drops_conflicting_xrefs(client):
    """If both records hold (system, system_id), survivor's wins; duplicate's drops."""
    survivor = _create(client, mrn="CONF-SURV")
    duplicate = _create(client, mrn="CONF-DUP")

    # Same (system, system_id) on both — duplicate's row must be deleted, not moved.
    client.post("/api/crossref", json={
        "master_id": survivor["id"], "system": "openmrs", "system_id": "omrs-dup-key",
    })
    client.post("/api/crossref", json={
        "master_id": duplicate["id"], "system": "openmrs", "system_id": "omrs-dup-key",
    })

    r = client.post(f"/api/patients/{survivor['id']}/merge",
                    json={"merge_id": duplicate["id"]})
    assert r.status_code == 200
    survivor_xrefs = client.get(f"/api/crossref?master_id={survivor['id']}").json()
    assert len(survivor_xrefs) == 1
    assert survivor_xrefs[0]["system_id"] == "omrs-dup-key"


def test_merge_marks_duplicate_merged_with_pointer(client, db):
    survivor = _create(client, mrn="POINT-SURV")
    duplicate = _create(client, mrn="POINT-DUP")
    client.post(f"/api/patients/{survivor['id']}/merge",
                json={"merge_id": duplicate["id"]})
    with db() as conn:
        row = conn.execute(
            "SELECT status, merged_into FROM master_patients WHERE id=?",
            (duplicate["id"],),
        ).fetchone()
    assert row["status"] == "merged"
    assert row["merged_into"] == survivor["id"]


def test_merge_resolves_pending_match_candidate_between_pair(client, db):
    """A pending match between the pair should auto-resolve to confirmed_match."""
    survivor = _create(client, mrn="MC-SURV")
    duplicate = _create(client, mrn="MC-DUP")

    a, b = sorted([survivor["id"], duplicate["id"]])
    with db() as conn:
        conn.execute(
            "INSERT INTO match_candidates(master_id_a,master_id_b,score)"
            " VALUES(?,?,?)",
            (a, b, 0.85),
        )

    client.post(f"/api/patients/{survivor['id']}/merge",
                json={"merge_id": duplicate["id"]})

    with db() as conn:
        row = conn.execute(
            "SELECT status FROM match_candidates WHERE master_id_a=? AND master_id_b=?",
            (a, b),
        ).fetchone()
    assert row["status"] == "confirmed_match"


def test_merge_missing_merge_id_returns_400(client):
    p = _create(client, mrn="MERGE-400-A")
    r = client.post(f"/api/patients/{p['id']}/merge", json={})
    assert r.status_code == 400


def test_merge_self_returns_400(client):
    p = _create(client, mrn="MERGE-SELF")
    r = client.post(f"/api/patients/{p['id']}/merge", json={"merge_id": p["id"]})
    assert r.status_code == 400


def test_merge_unknown_survivor_returns_404(client):
    duplicate = _create(client, mrn="MERGE-404-DUP")
    r = client.post(f"/api/patients/{uuid.uuid4()}/merge",
                    json={"merge_id": duplicate["id"]})
    assert r.status_code == 404


def test_merge_unknown_duplicate_returns_404(client):
    survivor = _create(client, mrn="MERGE-404-SURV")
    r = client.post(f"/api/patients/{survivor['id']}/merge",
                    json={"merge_id": str(uuid.uuid4())})
    assert r.status_code == 404


def test_merge_writes_audit_row(client):
    survivor = _create(client, mrn="MERGE-AUDIT-SURV")
    duplicate = _create(client, mrn="MERGE-AUDIT-DUP")
    client.post(f"/api/patients/{survivor['id']}/merge",
                json={"merge_id": duplicate["id"]})
    audit = client.get(f"/api/audit?master_id={survivor['id']}").json()
    assert any(row["action"] == "merged" for row in audit)
