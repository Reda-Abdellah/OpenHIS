"""
Tests for services/mpi/routers/fhir.py — the minimal FHIR R4 façade.

Two tiers:

1. DB-bound HTTP tests (client/db fixtures from conftest.py) — auto-skipped
   when PostgreSQL is unreachable, per the requires_pg/fresh_db pattern:
     - GET /fhir/Patient?identifier=<MRN> → searchset Bundle, identifiers
       carry the master MRN entry plus one entry per cross-reference
     - identifier=urn:openhis:openmrs|<system_id> resolves via cross_references
     - unknown identifier → HTTP 200 with an empty (total=0) Bundle
     - family+given+birthdate demographic search
     - GET /fhir/Patient/$ihe-pix → Parameters with targetIdentifier entries
       (queried identifier excluded) + a targetId reference
     - unknown sourceIdentifier → 404 OperationOutcome (PIXm requirement)
     - malformed sourceIdentifier (no system) → 400 OperationOutcome
     - targetSystem narrows the Parameters output

2. Pure-shape tests (@pytest.mark.no_db) that mock the DB layer so the
   Bundle/Parameters/token mapping logic runs without PostgreSQL.

Plus one real-auth test that boots MPI via tests/auth/harness.py with
DEV_MODE=false and asserts the /fhir routes are deny-by-default.
"""
import contextlib
import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).parent.parent.parent.parent
MPI_PATH = str(REPO_ROOT / "services" / "mpi")
AUTH_HARNESS_PATH = str(REPO_ROOT / "tests" / "auth")

MRN_SYSTEM = "urn:openhis:mpi:mrn"  # MPI_FHIR_SYSTEM default


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


def _add_xref(db, master_id: str, system: str, system_id: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO cross_references(master_id, system, system_id) VALUES(?,?,?)",
            (master_id, system, system_id),
        )


def _target_identifiers(parameters: dict) -> set[tuple[str, str]]:
    return {
        (p["valueIdentifier"]["system"], p["valueIdentifier"]["value"])
        for p in parameters["parameter"]
        if p["name"] == "targetIdentifier"
    }


def _target_refs(parameters: dict) -> list[str]:
    return [
        p["valueReference"]["reference"]
        for p in parameters["parameter"]
        if p["name"] == "targetId"
    ]


# ── GET /fhir/Patient (DB-bound) ──────────────────────────────────────────────


def test_search_by_bare_mrn_returns_bundle_with_all_identifiers(client, db):
    p = _create(client, mrn="FHIR-MRN-1", phone="+33600000001", address="1 Rue X")
    _add_xref(db, p["id"], "openmrs", "omrs-f1")
    _add_xref(db, p["id"], "openelis", "oe-f1")

    r = client.get("/fhir/Patient?identifier=FHIR-MRN-1")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 1
    entry = bundle["entry"][0]
    assert entry["fullUrl"] == f"Patient/{p['id']}"

    res = entry["resource"]
    assert res["resourceType"] == "Patient"
    assert res["id"] == p["id"]
    assert res["active"] is True
    idents = {(i["system"], i["value"]) for i in res["identifier"]}
    assert idents == {
        (MRN_SYSTEM, "FHIR-MRN-1"),
        ("urn:openhis:openmrs", "omrs-f1"),
        ("urn:openhis:openelis", "oe-f1"),
    }
    assert res["name"] == [{"family": "Doe", "given": ["Alice"]}]
    assert res["gender"] == "female"
    assert res["birthDate"] == "1980-01-01"
    assert res["telecom"] == [{"system": "phone", "value": "+33600000001"}]
    assert res["address"] == [{"text": "1 Rue X"}]


def test_search_by_mrn_system_token_form(client):
    p = _create(client, mrn="FHIR-MRN-2")
    r = client.get(f"/fhir/Patient?identifier={MRN_SYSTEM}|FHIR-MRN-2")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["total"] == 1
    assert bundle["entry"][0]["resource"]["id"] == p["id"]


def test_search_by_xref_token_resolves_via_cross_references(client, db):
    p = _create(client, mrn="FHIR-XREF-1")
    _add_xref(db, p["id"], "openmrs", "omrs-f2")
    r = client.get("/fhir/Patient?identifier=urn:openhis:openmrs|omrs-f2")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["total"] == 1
    assert bundle["entry"][0]["resource"]["id"] == p["id"]


def test_search_unknown_identifier_returns_empty_bundle_not_404(client):
    r = client.get("/fhir/Patient?identifier=DOES-NOT-EXIST")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 0
    assert bundle.get("entry", []) == []


def test_search_excludes_merged_records_for_mrn(client, db):
    p = _create(client, mrn="FHIR-MERGED-1")
    with db() as conn:
        conn.execute(
            "UPDATE master_patients SET status='merged' WHERE id=?", (p["id"],)
        )
    r = client.get("/fhir/Patient?identifier=FHIR-MERGED-1")
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_search_by_demographics(client):
    p = _create(client, mrn="FHIR-NB-1", firstname="Olivia",
                lastname="Brennan", birthdate="1975-03-22")
    _create(client, mrn="FHIR-NB-2", firstname="Sonic", lastname="Sega")
    r = client.get("/fhir/Patient?family=Brennan&given=Olivia&birthdate=1975-03-22")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["total"] == 1
    assert bundle["entry"][0]["resource"]["id"] == p["id"]


def test_search_without_usable_params_returns_400_operation_outcome(client):
    r = client.get("/fhir/Patient")
    assert r.status_code == 400
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["code"] == "invalid"


# ── GET /fhir/Patient/$ihe-pix (DB-bound) ─────────────────────────────────────


def test_ihe_pix_returns_other_identifiers_and_target_reference(client, db):
    p = _create(client, mrn="FHIR-PIX-1")
    _add_xref(db, p["id"], "openmrs", "omrs-pix-1")
    _add_xref(db, p["id"], "openelis", "oe-pix-1")

    r = client.get(
        "/fhir/Patient/$ihe-pix?sourceIdentifier=urn:openhis:openmrs|omrs-pix-1"
    )
    assert r.status_code == 200
    params = r.json()
    assert params["resourceType"] == "Parameters"
    # Queried identifier is excluded; MRN and the other xref are returned.
    assert _target_identifiers(params) == {
        (MRN_SYSTEM, "FHIR-PIX-1"),
        ("urn:openhis:openelis", "oe-pix-1"),
    }
    assert _target_refs(params) == [f"Patient/{p['id']}"]


def test_ihe_pix_query_by_mrn_system_returns_xrefs_only(client, db):
    p = _create(client, mrn="FHIR-PIX-2")
    _add_xref(db, p["id"], "openmrs", "omrs-pix-2")
    r = client.get(
        f"/fhir/Patient/$ihe-pix?sourceIdentifier={MRN_SYSTEM}|FHIR-PIX-2"
    )
    assert r.status_code == 200
    params = r.json()
    assert _target_identifiers(params) == {("urn:openhis:openmrs", "omrs-pix-2")}
    assert _target_refs(params) == [f"Patient/{p['id']}"]


def test_ihe_pix_unknown_source_identifier_returns_404_operation_outcome(client):
    r = client.get(
        "/fhir/Patient/$ihe-pix?sourceIdentifier=urn:openhis:openmrs|nope"
    )
    assert r.status_code == 404
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["code"] == "not-found"


def test_ihe_pix_source_identifier_without_system_returns_400(client):
    r = client.get("/fhir/Patient/$ihe-pix?sourceIdentifier=bare-value")
    assert r.status_code == 400
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["code"] == "invalid"


def test_ihe_pix_missing_source_identifier_returns_400(client):
    r = client.get("/fhir/Patient/$ihe-pix")
    assert r.status_code == 400
    assert r.json()["resourceType"] == "OperationOutcome"


def test_ihe_pix_target_system_narrows_output(client, db):
    p = _create(client, mrn="FHIR-PIX-3")
    _add_xref(db, p["id"], "openmrs", "omrs-pix-3")
    _add_xref(db, p["id"], "openelis", "oe-pix-3")

    r = client.get(
        "/fhir/Patient/$ihe-pix"
        "?sourceIdentifier=urn:openhis:openmrs|omrs-pix-3"
        f"&targetSystem={MRN_SYSTEM}"
    )
    assert r.status_code == 200
    params = r.json()
    assert _target_identifiers(params) == {(MRN_SYSTEM, "FHIR-PIX-3")}
    # targetId reference survives the filter
    assert _target_refs(params) == [f"Patient/{p['id']}"]


# ── pure-shape tests (no PostgreSQL) ──────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Maps SQL substrings to canned row lists (RealDictCursor-style dicts)."""

    def __init__(self, responses=()):
        self._responses = list(responses)

    def execute(self, sql, params=None):
        for needle, rows in self._responses:
            if needle in sql:
                return _FakeCursor(rows)
        return _FakeCursor([])


@pytest.fixture
def fhir():
    """Import services/mpi/routers/fhir.py fresh, without needing PostgreSQL.

    Mirrors the module-purge pattern of conftest.fresh_db (which no-ops for
    no_db tests) so the right service's `routers`/`database` modules load,
    and re-purges on teardown so later suites are unaffected.
    """
    def _purge():
        for m in [
            m for m in list(sys.modules)
            if m.startswith(("routers", "bus_consumer"))
            or m in ("main", "database", "matcher", "log_config", "jwt_auth")
        ]:
            sys.modules.pop(m, None)

    _purge()
    if MPI_PATH in sys.path:
        sys.path.remove(MPI_PATH)
    sys.path.insert(0, MPI_PATH)
    import routers.fhir as fhir_mod
    yield fhir_mod
    _purge()


@pytest.mark.no_db
def test_token_parses_system_and_value(fhir):
    assert fhir._token("urn:openhis:openmrs|abc") == ("urn:openhis:openmrs", "abc")
    assert fhir._token("bare-mrn") == (None, "bare-mrn")
    assert fhir._token("|value") == (None, "value")
    # only the first '|' splits — values may contain '|'
    assert fhir._token("sys|a|b") == ("sys", "a|b")


@pytest.mark.no_db
def test_token_rejects_empty_or_valueless_input(fhir):
    for bad in (None, "", "sys|"):
        with pytest.raises(HTTPException) as exc:
            fhir._token(bad)
        assert exc.value.status_code == 400


@pytest.mark.no_db
def test_to_fhir_patient_maps_full_demographics(fhir):
    row = {
        "id": "pid-1", "mrn": "MRN-1", "firstname": "Alice", "lastname": "Doe",
        "birthdate": "1980-01-01", "sex": "F", "phone": "+33600000000",
        "address": "1 Rue de la Paix", "status": "active",
    }
    db = _FakeDB([
        ("cross_references", [{"system": "openmrs", "system_id": "omrs-1"}]),
    ])
    patient = fhir._to_fhir_patient(db, row)
    assert patient == {
        "resourceType": "Patient",
        "id": "pid-1",
        "active": True,
        "identifier": [
            {"system": fhir.MPI_SYSTEM, "value": "MRN-1"},
            {"system": "urn:openhis:openmrs", "value": "omrs-1"},
        ],
        "name": [{"family": "Doe", "given": ["Alice"]}],
        "gender": "female",
        "birthDate": "1980-01-01",
        "telecom": [{"system": "phone", "value": "+33600000000"}],
        "address": [{"text": "1 Rue de la Paix"}],
    }


@pytest.mark.no_db
def test_to_fhir_patient_omits_optionals_when_null(fhir):
    row = {
        "id": "pid-2", "mrn": "MRN-2", "firstname": "Bob", "lastname": "Ray",
        "birthdate": None, "sex": None, "phone": None, "address": None,
        "status": "merged",
    }
    patient = fhir._to_fhir_patient(_FakeDB(), row)
    assert patient["active"] is False
    for absent in ("gender", "birthDate", "telecom", "address"):
        assert absent not in patient


@pytest.mark.no_db
@pytest.mark.parametrize("sex,expected", [("M", "male"), ("m", "male"),
                                          ("F", "female"), ("X", "unknown")])
def test_to_fhir_patient_gender_mapping(fhir, sex, expected):
    row = {"id": "p", "mrn": "M", "firstname": "A", "lastname": "B",
           "sex": sex, "status": "active"}
    assert fhir._to_fhir_patient(_FakeDB(), row)["gender"] == expected


@pytest.mark.no_db
def test_search_endpoint_shapes_empty_bundle_with_mocked_db(fhir, monkeypatch):
    monkeypatch.setattr(fhir, "get_db", lambda: contextlib.nullcontext(_FakeDB()))
    bundle = fhir.search_patient(identifier="NOPE")
    assert bundle == {
        "resourceType": "Bundle", "type": "searchset", "total": 0, "entry": [],
    }


@pytest.mark.no_db
def test_search_endpoint_400_outcome_when_no_usable_params(fhir):
    resp = fhir.search_patient(
        identifier=None, family=None, given=None, birthdate=None
    )
    assert resp.status_code == 400
    body = json.loads(resp.body)
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["severity"] == "error"
    assert body["issue"][0]["code"] == "invalid"


@pytest.mark.no_db
def test_ihe_pix_shapes_parameters_with_mocked_db(fhir, monkeypatch):
    master = {"id": "pid-9", "mrn": "MRN-9", "firstname": "A", "lastname": "B",
              "status": "active"}
    db = _FakeDB([
        ("cross_references WHERE system=", [{"master_id": "pid-9"}]),
        ("master_patients WHERE id=", [master]),
        ("cross_references WHERE master_id=", [
            {"system": "openmrs", "system_id": "omrs-9"},
            {"system": "openelis", "system_id": "oe-9"},
        ]),
    ])
    monkeypatch.setattr(fhir, "get_db", lambda: contextlib.nullcontext(db))

    params = fhir.ihe_pix(
        source_identifier="urn:openhis:openmrs|omrs-9", target_system=None
    )
    assert params["resourceType"] == "Parameters"
    assert _target_identifiers(params) == {
        (fhir.MPI_SYSTEM, "MRN-9"),
        ("urn:openhis:openelis", "oe-9"),
    }
    assert _target_refs(params) == ["Patient/pid-9"]


@pytest.mark.no_db
def test_ihe_pix_400_outcomes_without_db(fhir):
    missing = fhir.ihe_pix(source_identifier=None, target_system=None)
    assert missing.status_code == 400
    assert json.loads(missing.body)["resourceType"] == "OperationOutcome"

    bare = fhir.ihe_pix(source_identifier="no-system-here", target_system=None)
    assert bare.status_code == 400
    body = json.loads(bare.body)
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["code"] == "invalid"


# ── real-auth gating (tests/auth harness, no Postgres required) ──────────────


@pytest.mark.no_db
def test_fhir_routes_are_deny_by_default_with_real_auth(tmp_path):
    """Boot MPI with DEV_MODE=false via the auth harness: /fhir/* must 401
    without a token and pass the auth layers with a harness-signed token."""
    sys.path.insert(0, AUTH_HARNESS_PATH)
    try:
        import harness
        from fastapi.testclient import TestClient

        env = {"ROOT_PATH": "", "LOG_FORMAT": "text", "REDIS_URL": ""}
        with harness.isolated_service("mpi", env=env, init_db=False) as app:
            client = TestClient(app, raise_server_exceptions=False)

            assert client.get("/fhir/Patient?identifier=X").status_code == 401
            assert client.get(
                "/fhir/Patient/$ihe-pix?sourceIdentifier=a|b"
            ).status_code == 401

            foreign = {
                "Authorization": f"Bearer {harness.make_foreign_token(['clinician'])}"
            }
            assert client.get(
                "/fhir/Patient?identifier=X", headers=foreign
            ).status_code == 401

            # Valid token: auth layers must pass. The handler itself may still
            # 500 when PostgreSQL is unreachable in unit runs — that's fine,
            # we only assert the gate (mirrors ServiceSpec authorized_statuses=None).
            ok = client.get(
                "/fhir/Patient?identifier=X",
                headers=harness.auth_header(["clinician"]),
            )
            assert ok.status_code not in (401, 403)
    finally:
        sys.path.remove(AUTH_HARNESS_PATH)
        sys.modules.pop("harness", None)
