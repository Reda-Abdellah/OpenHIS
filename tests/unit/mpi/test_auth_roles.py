"""
T-05 — MPI role-gate enforcement with REAL JWT validation (no DEV_MODE).

Boots the MPI app via tests/auth/harness.py (DEV_MODE=false, in-memory JWKS)
and asserts per-route 401 (no token) / 403 (wrong role) for:

  GET  /api/patients/lookup                 clinician | radiologist | lab-tech | admin
  GET  /api/matching/candidates             clinician | admin
  POST /api/matching/run                    clinician | admin
  POST /api/matching/candidates/{id}/resolve clinician | admin
  POST /api/patients/{id}/merge             admin            (pre-existing gate)
  GET  /api/patients                        clinician | radiologist | lab-tech | admin

MPI handlers are PostgreSQL-bound and this suite must stay green without
Postgres (see tests/unit/mpi/conftest.py / DEF-003). The 401/403 cases never
reach a handler, so they are exact. The granted-role cases only assert the
auth layers passed (status not in 401/403): with Postgres absent the handler
500s, with it present it returns 200/404 — both prove authorization cleared.

The whole module is marked ``no_db`` so the autouse ``fresh_db`` fixture
neither skips (when PG is down) nor wipes the schema.
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_AUTH_DIR = Path(__file__).resolve().parents[2] / "auth"
if str(_AUTH_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTH_DIR))

import harness  # noqa: E402

pytestmark = pytest.mark.no_db

# Same DSN convention as tests/unit/mpi/conftest.py: localhost fails fast
# (connection refused) when Postgres is absent — no DNS / docker hostnames.
_TEST_DB_URL = os.environ.get(
    "MPI_DATABASE_URL", "postgresql://mpi:mpi@localhost:5432/mpi_test"
)

READ_ROLES = ("clinician", "radiologist", "lab-tech", "admin")
STEWARD_ROLES = ("clinician", "admin")

RESOLVE_BODY = {"decision": "confirmed_no_match", "reviewed_by": "auth-harness"}
MERGE_BODY = {"merge_id": "someone-else"}


@pytest.fixture(scope="module")
def mpi(tmp_path_factory):
    """MPI app booted once for the module with real auth enforced."""
    env = {
        "ROOT_PATH": "",
        "REDIS_URL": "",  # disable bus consumer, as in tests/unit/mpi/conftest.py
        "MPI_DATABASE_URL": _TEST_DB_URL,
    }
    with harness.isolated_service("mpi", env=env, init_db=False) as app:
        yield TestClient(app, raise_server_exceptions=False)


def _clears_auth(resp) -> None:
    assert resp.status_code not in (401, 403), (
        f"expected the auth layers to pass (any status but 401/403), "
        f"got {resp.status_code}: {resp.text}"
    )


# ── 401: no token ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("get", "/api/patients/lookup", None),
        ("get", "/api/matching/candidates", None),
        ("post", "/api/matching/run", None),
        ("post", "/api/matching/candidates/1/resolve", RESOLVE_BODY),
    ],
)
def test_routes_reject_missing_token(mpi, method, path, json_body):
    resp = mpi.request(method, path, json=json_body)
    assert resp.status_code == 401, resp.text


# ── 403: valid token, wrong role ─────────────────────────────────────────────

def test_lookup_rejects_roleless_and_nurse_tokens(mpi):
    assert mpi.get(
        "/api/patients/lookup", headers=harness.auth_header([])
    ).status_code == 403
    assert mpi.get(
        "/api/patients/lookup", headers=harness.auth_header(["nurse"])
    ).status_code == 403


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("get", "/api/matching/candidates", None),
        ("post", "/api/matching/run", None),
        ("post", "/api/matching/candidates/1/resolve", RESOLVE_BODY),
    ],
)
def test_matching_rejects_non_steward_roles(mpi, method, path, json_body):
    # Even a token holding BOTH clinical read roles must be denied —
    # match stewardship is clinician/admin only.
    resp = mpi.request(
        method, path, json=json_body,
        headers=harness.auth_header(["radiologist", "lab-tech", "nurse"]),
    )
    assert resp.status_code == 403, resp.text


def test_merge_stays_admin_only(mpi):
    # Pre-existing gate (regression net): clinicians cannot merge identities.
    resp = mpi.post(
        "/api/patients/some-id/merge", json=MERGE_BODY,
        headers=harness.auth_header(["clinician", "radiologist", "lab-tech"]),
    )
    assert resp.status_code == 403, resp.text


def test_patient_list_rejects_nurse_token(mpi):
    resp = mpi.get("/api/patients", headers=harness.auth_header(["nurse"]))
    assert resp.status_code == 403, resp.text


# ── granted roles clear the auth layers ──────────────────────────────────────
# (Handlers are Postgres-bound — see module docstring for why we only assert
#  "not 401/403" here.)

@pytest.mark.parametrize("role", READ_ROLES)
def test_lookup_clears_auth_for_every_read_role(mpi, role):
    _clears_auth(
        mpi.get(
            "/api/patients/lookup?mrn=AUTH-MRN-1",
            headers=harness.auth_header([role]),
        )
    )


@pytest.mark.parametrize("role", STEWARD_ROLES)
def test_matching_candidates_clears_auth_for_stewards(mpi, role):
    _clears_auth(
        mpi.get("/api/matching/candidates", headers=harness.auth_header([role]))
    )


@pytest.mark.parametrize("role", STEWARD_ROLES)
def test_matching_run_clears_auth_for_stewards(mpi, role):
    _clears_auth(
        mpi.post("/api/matching/run", headers=harness.auth_header([role]))
    )


def test_matching_resolve_clears_auth_for_stewards(mpi):
    _clears_auth(
        mpi.post(
            "/api/matching/candidates/1/resolve", json=RESOLVE_BODY,
            headers=harness.auth_header(["clinician"]),
        )
    )
