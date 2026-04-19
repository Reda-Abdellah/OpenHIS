"""
HTTP-level tests for services/mpi/routers/matching.py — the duplicate-detection
review queue.

Workflow under test:

    POST /api/matching/run                  → scan active patients, write
                                              match_candidates rows above the
                                              0.70 threshold (idempotent on
                                              (master_id_a, master_id_b))

    GET  /api/matching/candidates           → list pending (or any status)

    POST /api/matching/candidates/{id}/resolve
                                            → set status to confirmed_match
                                              or confirmed_no_match
"""
import uuid
import pytest


def _new(client, **overrides):
    body = {
        "mrn": f"M-{uuid.uuid4().hex[:6]}",
        "firstname": "Test", "lastname": "Patient",
        "birthdate": "1980-01-01", "sex": "M",
    }
    body.update(overrides)
    r = client.post("/api/patients", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ── POST /api/matching/run ────────────────────────────────────────────────────


def test_run_creates_candidate_for_demographic_duplicate(client):
    """Two patients with identical demographics (different MRN) → one candidate."""
    p1 = _new(client, firstname="John", lastname="Doe",
              birthdate="1980-01-01", sex="M", mrn="DUP-A")
    p2 = _new(client, firstname="John", lastname="Doe",
              birthdate="1980-01-01", sex="M", mrn="DUP-B")

    r = client.post("/api/matching/run")
    assert r.status_code == 200
    body = r.json()
    assert body["patients_scanned"] == 2
    assert body["candidates_created"] == 1

    rows = client.get("/api/matching/candidates").json()
    assert len(rows) == 1
    pair = {rows[0]["master_id_a"], rows[0]["master_id_b"]}
    assert pair == {p1["id"], p2["id"]}
    assert rows[0]["score"] >= 0.70


def test_run_no_candidates_for_distinct_patients(client):
    _new(client, firstname="Alice", lastname="Aaa",
         birthdate="1980-01-01", sex="F", mrn="NM-A")
    _new(client, firstname="Bob",   lastname="Zzz",
         birthdate="1995-12-31", sex="M", mrn="NM-B")
    r = client.post("/api/matching/run")
    assert r.json()["candidates_created"] == 0
    assert client.get("/api/matching/candidates").json() == []


def test_run_is_idempotent(client):
    """Re-running should not duplicate existing candidates (UNIQUE constraint)."""
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="IDEM-A")
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="IDEM-B")

    first  = client.post("/api/matching/run").json()
    second = client.post("/api/matching/run").json()

    assert first["candidates_created"] == 1
    assert second["candidates_created"] == 0
    assert len(client.get("/api/matching/candidates").json()) == 1


def test_run_skips_merged_patients(client, db):
    """Merged records must not appear in the matching pool."""
    p1 = _new(client, firstname="Same", lastname="Person",
              birthdate="1980-01-01", sex="M", mrn="SKIP-A")
    p2 = _new(client, firstname="Same", lastname="Person",
              birthdate="1980-01-01", sex="M", mrn="SKIP-B")
    with db() as conn:
        conn.execute(
            "UPDATE master_patients SET status='merged', merged_into=? WHERE id=?",
            (p1["id"], p2["id"]),
        )
    r = client.post("/api/matching/run")
    assert r.json()["patients_scanned"] == 1
    assert r.json()["candidates_created"] == 0


# ── GET /api/matching/candidates ──────────────────────────────────────────────


def test_list_candidates_empty(client):
    assert client.get("/api/matching/candidates").json() == []


def test_list_candidates_includes_join_columns(client):
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="JOIN-A")
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="JOIN-B")
    client.post("/api/matching/run")
    rows = client.get("/api/matching/candidates").json()
    assert rows
    row = rows[0]
    # Demographics joined in for the review UI
    assert "name_a" in row and "mrn_a" in row
    assert "name_b" in row and "mrn_b" in row
    assert "John" in row["name_a"]


def test_list_candidates_filter_by_status(client):
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="FS-A")
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="FS-B")
    client.post("/api/matching/run")
    cid = client.get("/api/matching/candidates").json()[0]["id"]
    client.post(f"/api/matching/candidates/{cid}/resolve",
                json={"decision": "confirmed_no_match"})

    pending = client.get("/api/matching/candidates?status=pending").json()
    assert pending == []
    resolved = client.get("/api/matching/candidates?status=confirmed_no_match").json()
    assert len(resolved) == 1


# ── POST /api/matching/candidates/{cid}/resolve ───────────────────────────────


def test_resolve_confirmed_no_match_updates_status(client):
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="RNM-A")
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="RNM-B")
    client.post("/api/matching/run")
    cid = client.get("/api/matching/candidates").json()[0]["id"]

    r = client.post(f"/api/matching/candidates/{cid}/resolve",
                    json={"decision": "confirmed_no_match",
                          "reviewed_by": "alice"})
    assert r.status_code == 200
    assert r.json()["decision"] == "confirmed_no_match"


def test_resolve_invalid_decision_returns_400(client):
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="BAD-A")
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="BAD-B")
    client.post("/api/matching/run")
    cid = client.get("/api/matching/candidates").json()[0]["id"]

    r = client.post(f"/api/matching/candidates/{cid}/resolve",
                    json={"decision": "maybe"})
    assert r.status_code == 400


def test_resolve_unknown_candidate_returns_404(client):
    r = client.post("/api/matching/candidates/9999999/resolve",
                    json={"decision": "confirmed_no_match"})
    assert r.status_code == 404


def test_resolve_records_reviewer(client, db):
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="REV-A")
    _new(client, firstname="John", lastname="Doe",
         birthdate="1980-01-01", sex="M", mrn="REV-B")
    client.post("/api/matching/run")
    cid = client.get("/api/matching/candidates").json()[0]["id"]
    client.post(f"/api/matching/candidates/{cid}/resolve",
                json={"decision": "confirmed_no_match", "reviewed_by": "bob"})
    with db() as conn:
        row = conn.execute(
            "SELECT reviewed_by, reviewedat FROM match_candidates WHERE id=?",
            (cid,),
        ).fetchone()
    assert row["reviewed_by"] == "bob"
    assert row["reviewedat"]
