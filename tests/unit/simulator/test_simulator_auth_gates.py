"""
T-06 — simulator lockdown, exercised with REAL auth.

Two protections, both asserted here:

1. Dev-only guard: importing the simulator with ENV != development must
   sys.exit (the simulator injects synthetic DICOM into Orthanc — it is
   never a production service).
2. JWT + role gate: with DEV_MODE=false the SDK middleware protects every
   /api/* route, and POST /api/generate additionally requires the
   admin or radiologist role (401 / 403 / 200 triple below).

Boots the app through tests/auth/harness.py (RS256 tokens against an
in-memory JWKS) — the sibling tests in this directory keep running in the
DEV_MODE=true world.
"""
import sys
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

_AUTH_DIR = str(Path(__file__).resolve().parents[2] / "auth")
if _AUTH_DIR not in sys.path:
    sys.path.insert(0, _AUTH_DIR)

import harness  # noqa: E402  (tests/auth/harness.py)

ORTHANC_URL = "http://orthanc-auth-test:9997"
GENERATE_PAYLOAD = {
    "modality": "CR",
    "params": {"body_part": "CHEST", "kVp": 120, "mAs": 5},
    "patient": {"patient_name": "Auth^Gate", "patient_id": "AUTH001",
                "patient_birthdate": "19900101", "patient_sex": "M"},
}


@pytest.fixture(scope="module")
def sim():
    """Simulator app booted with real JWT validation enforced."""
    env = {"ROOT_PATH": "", "ORTHANC_URL": ORTHANC_URL}
    with harness.isolated_service("simulator", env=env) as app:
        yield TestClient(app, raise_server_exceptions=False)


# ── 1. dev-only ENV guard ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_env", ["staging", "production", "Production"])
def test_simulator_refuses_non_development_env(bad_env):
    with pytest.raises(SystemExit) as excinfo:
        with harness.isolated_service("simulator", env={"ENV": bad_env}):
            pass  # pragma: no cover — import must never succeed
    assert "dev-only" in str(excinfo.value.code)


def test_simulator_boots_in_development_env(sim):
    r = sim.get("/api/health")
    assert r.status_code == 200
    assert r.json()["service"] == "simulator"


# ── 2. JWT middleware + role gate on /api/generate ──────────────────────────

def test_generate_requires_token(sim):
    r = sim.post("/api/generate", json=GENERATE_PAYLOAD)
    assert r.status_code == 401


def test_generate_rejects_garbage_token(sim):
    r = sim.post("/api/generate", json=GENERATE_PAYLOAD,
                 headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


def test_generate_rejects_foreign_signature(sim):
    token = harness.make_foreign_token(["admin"])
    r = sim.post("/api/generate", json=GENERATE_PAYLOAD,
                 headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_generate_rejects_wrong_role(sim):
    r = sim.post("/api/generate", json=GENERATE_PAYLOAD,
                 headers=harness.auth_header(["nurse", "clinician"]))
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["admin", "radiologist"])
@respx.mock
def test_generate_accepts_granted_role(sim, role):
    respx.post(f"{ORTHANC_URL}/instances").mock(
        return_value=httpx.Response(200, json={"ID": "auth-gate-instance"})
    )
    r = sim.post("/api/generate", json=GENERATE_PAYLOAD,
                 headers=harness.auth_header([role]))
    assert r.status_code == 200, (
        f"POST /api/generate with role {role!r} must return 200, "
        f"got {r.status_code}: {r.text[:200]}"
    )
    assert r.json()["modality"] == "CR"


def test_jobs_listing_requires_token_but_no_role(sim):
    assert sim.get("/api/jobs").status_code == 401
    r = sim.get("/api/jobs", headers=harness.auth_header(["nurse"]))
    assert r.status_code == 200


def test_health_stays_public(sim):
    assert sim.get("/api/health").status_code == 200
