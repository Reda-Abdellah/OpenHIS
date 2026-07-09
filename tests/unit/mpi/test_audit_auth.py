"""
T-03 step 3 — MPI /api/audit admin gate + limit cap with REAL JWT validation.

The audit log exposes master_ids, MRNs and merge history, so the read endpoint
must be admin-only and its `limit` query param must be capped (ge=1, le=1000).

Boots the MPI app via tests/auth/harness.py (DEV_MODE=false, in-memory JWKS),
following the tests/unit/mpi/test_auth_roles.py pattern:

  - 401 when no token is presented
  - 403 for valid tokens without the admin role (incl. multi-role clinical tokens)
  - 422 for out-of-range `limit` values (validated before the handler, so no
    PostgreSQL is needed — but only after the admin gate has cleared)
  - 200 + JSON list for an admin token when PostgreSQL is reachable
    (DB-bound, guarded by the requires_pg marker from conftest.py)

The module is marked ``no_db`` so the autouse ``fresh_db`` fixture neither
skips (when PG is down) nor wipes the schema; the single DB-bound test does
its own schema init via the harness (init_db=True) and is skipped without PG.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_AUTH_DIR = Path(__file__).resolve().parents[2] / "auth"
if str(_AUTH_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTH_DIR))

import harness  # noqa: E402

from .conftest import requires_pg  # noqa: E402

pytestmark = pytest.mark.no_db

# Same DSN convention as tests/unit/mpi/conftest.py: localhost fails fast
# (connection refused) when Postgres is absent — no DNS / docker hostnames.
_TEST_DB_URL = os.environ.get(
    "MPI_DATABASE_URL", "postgresql://mpi:mpi@localhost:5432/mpi_test"
)

_ENV = {
    "ROOT_PATH": "",
    "REDIS_URL": "",  # disable bus consumer, as in tests/unit/mpi/conftest.py
    "LOG_FORMAT": "text",
    "MPI_DATABASE_URL": _TEST_DB_URL,
}

NON_ADMIN_ROLES = ("clinician", "radiologist", "lab-tech", "nurse")


@pytest.fixture(scope="module")
def mpi():
    """MPI app booted once for the module with real auth enforced (no DB init)."""
    with harness.isolated_service("mpi", env=_ENV, init_db=False) as app:
        yield TestClient(app, raise_server_exceptions=False)


# ── 401: no token ────────────────────────────────────────────────────────────

def test_audit_rejects_missing_token(mpi):
    resp = mpi.get("/api/audit")
    assert resp.status_code == 401, resp.text


def test_audit_rejects_foreign_issuer_token(mpi):
    resp = mpi.get(
        "/api/audit",
        headers={"Authorization": f"Bearer {harness.make_foreign_token(['admin'])}"},
    )
    assert resp.status_code == 401, resp.text


# ── 403: valid token, wrong role ─────────────────────────────────────────────

@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_audit_rejects_single_non_admin_roles(mpi, role):
    resp = mpi.get("/api/audit", headers=harness.auth_header([role]))
    assert resp.status_code == 403, resp.text


def test_audit_rejects_roleless_token(mpi):
    resp = mpi.get("/api/audit", headers=harness.auth_header([]))
    assert resp.status_code == 403, resp.text


def test_audit_rejects_combined_clinical_roles(mpi):
    # Even a token holding EVERY clinical role must be denied — audit reads
    # are admin-only.
    resp = mpi.get(
        "/api/audit", headers=harness.auth_header(list(NON_ADMIN_ROLES))
    )
    assert resp.status_code == 403, resp.text


# ── 422: limit cap (validated before the handler — no PostgreSQL needed) ─────

@pytest.mark.parametrize("limit", [0, -1, 1001, 99999999])
def test_audit_limit_out_of_range_is_422_for_admin(mpi, limit):
    resp = mpi.get(f"/api/audit?limit={limit}", headers=harness.auth_header(["admin"]))
    assert resp.status_code == 422, resp.text


def test_audit_limit_cap_does_not_leak_to_non_admins(mpi):
    # The role gate must fire before query validation: a non-admin probing
    # limit=99999999 sees 403, not 422.
    resp = mpi.get(
        "/api/audit?limit=99999999", headers=harness.auth_header(["nurse"])
    )
    assert resp.status_code == 403, resp.text


# ── 200: admin token, DB-bound (skipped when PostgreSQL is unreachable) ──────

@requires_pg
def test_audit_returns_rows_for_admin_with_db():
    """Admin token + reachable PostgreSQL → 200 with the seeded audit row."""
    with harness.isolated_service("mpi", env=_ENV, init_db=True) as app:
        client = TestClient(app, raise_server_exceptions=False)

        # Seed a uniquely-identified row through the service's own DB layer
        # (the no_db marker means fresh_db did not wipe/own this schema, so
        # filter by a fresh master_id instead of asserting global contents).
        from database import get_db
        marker = str(uuid.uuid4())
        with get_db() as db:
            db.execute(
                "INSERT INTO audit_log(master_id,action,details) VALUES(?,?,?)",
                (marker, "created", "audit-auth-harness"),
            )
        try:
            resp = client.get(
                f"/api/audit?master_id={marker}",
                headers=harness.auth_header(["admin"]),
            )
            assert resp.status_code == 200, resp.text
            rows = resp.json()
            assert isinstance(rows, list)
            assert {r["master_id"] for r in rows} == {marker}

            # In-range limit values pass validation for admins.
            ok = client.get(
                "/api/audit?limit=1000", headers=harness.auth_header(["admin"])
            )
            assert ok.status_code == 200, ok.text
        finally:
            with get_db() as db:
                db.execute("DELETE FROM audit_log WHERE master_id=?", (marker,))
