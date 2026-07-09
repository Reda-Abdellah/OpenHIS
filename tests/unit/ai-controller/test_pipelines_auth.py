"""
Role-gate enforcement on the ai-controller routers (T-02).

Boots the real service app with DEV_MODE=false through the tests/auth
harness (RS256 tokens validated against an in-memory JWKS — no Keycloak,
no network) and asserts the deny-by-default behaviour:

  * no token            -> 401 (JWTMiddleware)
  * wrong-role token    -> 403 (require_roles)
  * admin token         -> 201 (handler reached)

Plus the PipelineCreate.id pattern guard and the jobs/saveback POST gates.
"""
import sys
from pathlib import Path

import pytest
import respx
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import Response

# tests/auth is only on sys.path while the tests/auth suite runs — add it
# explicitly so this file can use the harness from tests/unit as well.
_AUTH_DIR = str(Path(__file__).resolve().parents[2] / "auth")
if _AUTH_DIR not in sys.path:
    sys.path.insert(0, _AUTH_DIR)

import harness  # noqa: E402

PIPELINE_PAYLOAD = {
    "id": "auth-pipe",
    "name": "Auth Test Pipeline",
    "description": "T-02 role-gate test",
    "docker_image": "openhis/poc-xray:latest",
}


@pytest.fixture
def ai_client(tmp_path):
    """ai-controller TestClient with real JWT validation enforced."""
    env = {
        "DB_PATH": f"{tmp_path}/ai.db",
        "ROOT_PATH": "",
        "ORTHANC_URL": "http://localhost:19999",
        "JOBS_DATA_DIR": f"{tmp_path}/ai_jobs",
        "FHIR_BRIDGE_URL": "",
        "REDIS_URL": "",
        "OPENELIS_URL": "",
    }
    with harness.isolated_service("ai-controller", env=env, init_db=True) as app:
        yield TestClient(app, raise_server_exceptions=False)


# ── /api/pipelines (the three canonical cases) ────────────────────────────────

def test_create_pipeline_without_token_is_401(ai_client):
    resp = ai_client.post("/api/pipelines", json=PIPELINE_PAYLOAD)
    assert resp.status_code == 401


def test_create_pipeline_with_clinician_token_is_403(ai_client):
    resp = ai_client.post(
        "/api/pipelines", json=PIPELINE_PAYLOAD,
        headers=harness.auth_header(["clinician"]),
    )
    assert resp.status_code == 403


def test_create_pipeline_with_admin_token_is_201(ai_client):
    resp = ai_client.post(
        "/api/pipelines", json=PIPELINE_PAYLOAD,
        headers=harness.auth_header(["admin"]),
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == PIPELINE_PAYLOAD["id"]


def test_pipeline_id_pattern_rejects_path_traversal(ai_client):
    bad = dict(PIPELINE_PAYLOAD, id="../escape")
    resp = ai_client.post(
        "/api/pipelines", json=bad, headers=harness.auth_header(["admin"]),
    )
    assert resp.status_code == 422


def test_delete_pipeline_requires_admin(ai_client):
    resp = ai_client.delete(
        "/api/pipelines/poc-xray", headers=harness.auth_header(["radiologist"]),
    )
    assert resp.status_code == 403


# ── /api/rules — writes are admin-only ────────────────────────────────────────

def test_create_rule_with_radiologist_token_is_403(ai_client):
    resp = ai_client.post(
        "/api/rules",
        json={"pipeline_id": "poc-xray", "name": "nope"},
        headers=harness.auth_header(["radiologist"]),
    )
    assert resp.status_code == 403


def test_create_rule_with_admin_token_is_201(ai_client):
    resp = ai_client.post(
        "/api/rules",
        json={"pipeline_id": "poc-xray", "name": "T-02 gate check"},
        headers=harness.auth_header(["admin"]),
    )
    assert resp.status_code == 201


# ── /api/jobs and /api/saveback — patient tokens cannot trigger ──────────────

def test_trigger_job_with_patient_token_is_403(ai_client):
    resp = ai_client.post(
        "/api/jobs",
        json={"pipeline_id": "poc-xray"},
        headers=harness.auth_header(["patient"]),
    )
    assert resp.status_code == 403


def test_trigger_job_with_clinician_token_clears_auth(ai_client):
    # Unknown pipeline -> 404 proves both auth layers let the call through.
    resp = ai_client.post(
        "/api/jobs",
        json={"pipeline_id": "does-not-exist"},
        headers=harness.auth_header(["clinician"]),
    )
    assert resp.status_code == 404


def test_saveback_with_patient_token_is_403(ai_client):
    resp = ai_client.post(
        "/api/saveback",
        json={"job_id": "j1", "artifact_id": 1},
        headers=harness.auth_header(["patient"]),
    )
    assert resp.status_code == 403


# ── /api/trigger-instance — internal-sync/admin only (orthanc plugin SA) ──────

def test_trigger_instance_without_token_is_401(ai_client):
    resp = ai_client.post("/api/trigger-instance", json={"instance_id": "inst-1"})
    assert resp.status_code == 401


def test_trigger_instance_with_patient_token_is_403(ai_client):
    resp = ai_client.post(
        "/api/trigger-instance",
        json={"instance_id": "inst-1"},
        headers=harness.auth_header(["patient"]),
    )
    assert resp.status_code == 403


def test_trigger_instance_with_clinician_token_is_403(ai_client):
    # Even clinicians may not call the webhook — it is for the plugin SA only.
    resp = ai_client.post(
        "/api/trigger-instance",
        json={"instance_id": "inst-1"},
        headers=harness.auth_header(["clinician"]),
    )
    assert resp.status_code == 403


@respx.mock
def test_trigger_instance_with_internal_sync_token_is_202(ai_client):
    # ORTHANC_URL is pinned to http://localhost:19999 by the fixture env.
    respx.get("http://localhost:19999/instances/inst-1").mock(
        return_value=Response(200, json={"ParentSeries": "series-1"})
    )
    respx.get("http://localhost:19999/series/series-1").mock(
        return_value=Response(
            200,
            json={"MainDicomTags": {"Modality": "MR", "BodyPartExamined": "BRAIN"}},
        )
    )
    resp = ai_client.post(
        "/api/trigger-instance",
        json={"instance_id": "inst-1"},
        headers=harness.auth_header(["internal-sync"]),
    )
    assert resp.status_code == 202
    body = resp.json()
    # MR/BRAIN matches no seeded auto-trigger rule — handler ran, no job spawned.
    assert body == {"launched": 0, "series_id": "series-1", "jobs": []}


# ── /api/jobs reads + DELETE — patient tokens locked out ─────────────────────

def test_list_jobs_with_patient_token_is_403(ai_client):
    resp = ai_client.get("/api/jobs", headers=harness.auth_header(["patient"]))
    assert resp.status_code == 403


def test_list_jobs_with_clinician_token_is_200(ai_client):
    resp = ai_client.get("/api/jobs", headers=harness.auth_header(["clinician"]))
    assert resp.status_code == 200


def test_get_job_with_patient_token_is_403(ai_client):
    resp = ai_client.get("/api/jobs/some-id", headers=harness.auth_header(["patient"]))
    assert resp.status_code == 403


def test_delete_job_with_patient_token_is_403(ai_client):
    resp = ai_client.delete(
        "/api/jobs/some-id", headers=harness.auth_header(["patient"]),
    )
    assert resp.status_code == 403


def test_delete_job_with_clinician_token_is_403(ai_client):
    # Destructive: admin/radiologist only — clinician read access does not extend here.
    resp = ai_client.delete(
        "/api/jobs/some-id", headers=harness.auth_header(["clinician"]),
    )
    assert resp.status_code == 403


def test_delete_job_with_radiologist_token_is_204(ai_client):
    resp = ai_client.delete(
        "/api/jobs/some-id", headers=harness.auth_header(["radiologist"]),
    )
    assert resp.status_code == 204


# ── /api/artifacts — full router gated (PHI: reports, secondary captures) ────

def test_artifact_download_without_token_is_401(ai_client):
    resp = ai_client.get("/api/artifacts/1/download")
    assert resp.status_code == 401


def test_artifact_download_with_patient_token_is_403(ai_client):
    resp = ai_client.get(
        "/api/artifacts/1/download", headers=harness.auth_header(["patient"]),
    )
    assert resp.status_code == 403


def test_artifact_list_with_patient_token_is_403(ai_client):
    resp = ai_client.get(
        "/api/artifacts/job/j1", headers=harness.auth_header(["patient"]),
    )
    assert resp.status_code == 403


def test_artifact_get_with_clinician_token_clears_auth(ai_client):
    # 404 (not 401/403) proves the gate admits clinicians and the handler ran.
    resp = ai_client.get(
        "/api/artifacts/999999", headers=harness.auth_header(["clinician"]),
    )
    assert resp.status_code == 404


# ── /api/saveback events + /api/orthanc/series reads ─────────────────────────

def test_saveback_events_with_patient_token_is_403(ai_client):
    resp = ai_client.get(
        "/api/saveback/job/j1", headers=harness.auth_header(["patient"]),
    )
    assert resp.status_code == 403


def test_orthanc_series_with_patient_token_is_403(ai_client):
    resp = ai_client.get(
        "/api/orthanc/series", headers=harness.auth_header(["patient"]),
    )
    assert resp.status_code == 403


# ── deny-by-default net: every API route must carry a require_roles gate ─────

# Public by design: health probe, static UI shell, schema/docs, Prometheus scrape.
UNGATED_ALLOWLIST = {
    "/api/health",
    "/",
    "/metrics",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


def test_every_route_is_role_gated_except_health_and_static(ai_client):
    ungated = []
    for route in ai_client.app.routes:
        if not isinstance(route, APIRoute):
            continue  # static mount, websockets, etc.
        if route.path in UNGATED_ALLOWLIST:
            continue
        gated = any(
            getattr(dep.call, "__qualname__", "").startswith("require_roles")
            for dep in route.dependant.dependencies
        )
        if not gated:
            ungated.append(f"{sorted(route.methods)} {route.path}")
    assert not ungated, f"Routes missing a require_roles gate: {ungated}"
