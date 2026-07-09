"""
T-05 — RIS role-gate enforcement with REAL JWT validation (no DEV_MODE).

Boots the RIS app via tests/auth/harness.py (DEV_MODE=false, in-memory JWKS)
and asserts per-route 401 (no token) / 403 (wrong role) / success (granted
role) for the orders and patients routers:

  GET    /api/orders/{id}        clinician | radiologist | admin
  PUT    /api/orders/{id}        radiologist | admin
  DELETE /api/orders/{id}        admin
  GET    /api/patients[, /{id}]  clinician | radiologist | lab-tech | admin
  POST   /api/patients           clinician | admin
  PATCH  /api/patients/{id}      clinician | admin
  POST   /api/patients/from-ehr  clinician | admin
  DELETE /api/patients/{id}      admin

The catalog-driven sweep in tests/auth covers one probe path per service;
this module covers every newly gated RIS route. The autouse ``fresh_db``
fixture from this package's conftest still runs (DEV_MODE=true world), but
the module-scoped harness app is imported first with DEV_MODE=false baked
into its module constants, so enforcement here is unaffected.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_AUTH_DIR = Path(__file__).resolve().parents[2] / "auth"
if str(_AUTH_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTH_DIR))

import harness  # noqa: E402


@pytest.fixture(scope="module")
def ris(tmp_path_factory):
    """RIS app booted once for the module with real auth enforced."""
    tmp = str(tmp_path_factory.mktemp("ris_auth_roles"))
    env = {
        "DB_PATH": f"{tmp}/ris.db",
        "ROOT_PATH": "",
        "FHIR_BRIDGE_URL": "",
        "OPENMRS_URL": "http://localhost:19999",
        "OPENMRS_USER": "admin",
        "OPENMRS_PASS": "admin",
        "POLL_INTERVAL_S": "99999",
    }
    with harness.isolated_service("ris", env=env, init_db=True) as app:
        yield TestClient(app, raise_server_exceptions=False)


def _mk_patient(client: TestClient, mrn: str) -> dict:
    r = client.post(
        "/api/patients",
        json={"mrn": mrn, "patient_name": f"Auth Roles {mrn}"},
        headers=harness.auth_header(["admin"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


# ── orders ───────────────────────────────────────────────────────────────────

ORDER_BODY = {"status": "IN_PROGRESS"}


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("get", "/api/orders/1", None),
        ("put", "/api/orders/1", ORDER_BODY),
        ("delete", "/api/orders/1", None),
    ],
)
def test_order_item_routes_reject_missing_token(ris, method, path, json_body):
    resp = ris.request(method, path, json=json_body)
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize(
    "method,path,json_body,denied_roles",
    [
        # GET item: nurse has no read access at all.
        ("get", "/api/orders/1", None, ["nurse"]),
        # PUT item: clinicians may read orders but not modify them.
        ("put", "/api/orders/1", ORDER_BODY, ["clinician", "nurse"]),
        # DELETE (cancel): admin-only — even radiologists are denied.
        ("delete", "/api/orders/1", None, ["radiologist", "clinician"]),
    ],
)
def test_order_item_routes_reject_wrong_role(ris, method, path, json_body, denied_roles):
    resp = ris.request(
        method, path, json=json_body, headers=harness.auth_header(denied_roles)
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize(
    "method,path,json_body,granted_role",
    [
        ("get", "/api/orders/999", None, "clinician"),
        ("get", "/api/orders/999", None, "radiologist"),
        ("put", "/api/orders/999", ORDER_BODY, "radiologist"),
        ("delete", "/api/orders/999", None, "admin"),
    ],
)
def test_order_item_routes_clear_auth_with_granted_role(ris, method, path, json_body, granted_role):
    # No order with id 999 exists — 404 proves the auth layers let us through
    # to the handler.
    resp = ris.request(
        method, path, json=json_body, headers=harness.auth_header([granted_role])
    )
    assert resp.status_code == 404, resp.text


def test_order_full_lifecycle_with_granted_roles(ris):
    patient = _mk_patient(ris, "AUTH-ORD-1")
    created = ris.post(
        "/api/orders",
        json={"patient_id": patient["id"], "modality": "CT"},
        headers=harness.auth_header(["radiologist"]),
    )
    assert created.status_code == 201, created.text
    oid = created.json()["id"]

    assert ris.get(
        f"/api/orders/{oid}", headers=harness.auth_header(["clinician"])
    ).status_code == 200
    assert ris.put(
        f"/api/orders/{oid}", json=ORDER_BODY,
        headers=harness.auth_header(["radiologist"]),
    ).status_code == 200
    assert ris.delete(
        f"/api/orders/{oid}", headers=harness.auth_header(["admin"])
    ).status_code == 204


# ── patients ─────────────────────────────────────────────────────────────────

CREATE_BODY = {"mrn": "AUTH-PT-X", "patient_name": "Auth Denied"}
FROM_EHR_BODY = {"mrn": "AUTH-EHR-X", "patient_name": "Auth Denied"}


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("get", "/api/patients", None),
        ("get", "/api/patients/1", None),
        ("post", "/api/patients", CREATE_BODY),
        ("patch", "/api/patients/1", {"sex": "F"}),
        ("delete", "/api/patients/1", None),
        ("post", "/api/patients/from-ehr", FROM_EHR_BODY),
    ],
)
def test_patient_routes_reject_missing_token(ris, method, path, json_body):
    resp = ris.request(method, path, json=json_body)
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize(
    "method,path,json_body,denied_roles",
    [
        # Reads: any clinical role passes, plain nurse does not.
        ("get", "/api/patients", None, ["nurse"]),
        ("get", "/api/patients/1", None, ["nurse"]),
        # Writes: read-only roles (radiologist, lab-tech) are denied.
        ("post", "/api/patients", CREATE_BODY, ["radiologist", "lab-tech"]),
        ("patch", "/api/patients/1", {"sex": "F"}, ["radiologist", "lab-tech"]),
        ("post", "/api/patients/from-ehr", FROM_EHR_BODY, ["radiologist", "lab-tech"]),
        # Delete: admin-only — even clinicians are denied.
        ("delete", "/api/patients/1", None, ["clinician", "radiologist", "lab-tech"]),
    ],
)
def test_patient_routes_reject_wrong_role(ris, method, path, json_body, denied_roles):
    resp = ris.request(
        method, path, json=json_body, headers=harness.auth_header(denied_roles)
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("role", ["clinician", "radiologist", "lab-tech", "admin"])
def test_patient_list_allows_every_read_role(ris, role):
    resp = ris.get("/api/patients", headers=harness.auth_header([role]))
    assert resp.status_code == 200, resp.text


def test_patient_write_routes_accept_clinician(ris):
    created = ris.post(
        "/api/patients",
        json={"mrn": "AUTH-PT-1", "patient_name": "Auth Roles Create"},
        headers=harness.auth_header(["clinician"]),
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    assert ris.get(
        f"/api/patients/{pid}", headers=harness.auth_header(["lab-tech"])
    ).status_code == 200
    assert ris.patch(
        f"/api/patients/{pid}", json={"sex": "F"},
        headers=harness.auth_header(["clinician"]),
    ).status_code == 200

    upsert = ris.post(
        "/api/patients/from-ehr",
        json={"mrn": "AUTH-EHR-1", "patient_name": "Auth Roles EHR"},
        headers=harness.auth_header(["clinician"]),
    )
    assert upsert.status_code == 200, upsert.text

    assert ris.delete(
        f"/api/patients/{pid}", headers=harness.auth_header(["admin"])
    ).status_code == 204
