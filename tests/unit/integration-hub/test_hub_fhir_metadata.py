"""
Unit tests for GET /fhir/metadata — the hub's FHIR R4 CapabilityStatement.

Asserts the discovery contract FHIR clients rely on (resourceType, status,
fhirVersion, declared resource types) and validates the full payload with
the `fhir.resources` library (TODO 5.2, docs/task_planning/4_TODO_list.md).

Truthfulness guard: the hub is a façade/poller whose only /fhir endpoint is
GET /metadata, so the statement must NOT advertise per-resource REST
interactions (read/create/update) that would 404 if a conformance client
tried them — resource entries carry documentation of the poll/push flow
instead.

The endpoint is intentionally token-free: a CapabilityStatement carries no
PHI, and FHIR clients fetch /metadata before they can authenticate. That
exemption is enforced (not just incidental) via the JWTMiddleware
extra_public_prefixes in app/main.py — TestEnforcedAuthExemption boots the
hub through the tests/auth harness (DEV_MODE=false, real RS256 validation)
and proves a token-less GET still returns 200 while sibling routes 401.
"""

import sys
from pathlib import Path

import pytest

_AUTH_DIR = str(Path(__file__).resolve().parents[2] / "auth")
if _AUTH_DIR not in sys.path:
    sys.path.insert(0, _AUTH_DIR)

import harness  # noqa: E402  (tests/auth/harness.py)

# Exactly the resource types the hub actually moves (poll/push) — equality,
# not subset, so the statement and the hub's real flows cannot drift apart
# silently. MedicationRequest was dropped: the hub handles it nowhere.
EXPECTED_RESOURCE_TYPES = {
    "Patient",
    "ServiceRequest",
    "DiagnosticReport",
    "Observation",
    "ImagingStudy",
}


def test_metadata_returns_fhir_json(client) -> None:
    resp = client.get("/fhir/metadata")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/fhir+json")


def test_metadata_capability_statement_core_fields(client) -> None:
    body = client.get("/fhir/metadata").json()
    assert body["resourceType"] == "CapabilityStatement"
    assert body["status"] == "active"
    assert body["fhirVersion"] == "4.0.1"
    assert body["kind"] == "instance"


def test_metadata_declares_handled_resource_types(client) -> None:
    body = client.get("/fhir/metadata").json()
    declared = {r["type"] for r in body["rest"][0]["resource"]}
    assert declared == EXPECTED_RESOURCE_TYPES


def test_metadata_declares_no_unserved_rest_interactions(client) -> None:
    """The hub serves no per-resource FHIR REST endpoints — the statement
    must not advertise interactions a client would 404 on, and must say how
    each resource actually flows instead."""
    rest = client.get("/fhir/metadata").json()["rest"][0]
    assert "documentation" in rest, "rest[0] must document the façade model"
    for resource in rest["resource"]:
        assert "interaction" not in resource, (
            f"{resource['type']} advertises REST interactions the hub "
            "does not expose"
        )
        assert resource.get("documentation"), (
            f"{resource['type']} must document its poll/push flow"
        )


def test_metadata_date_is_stable_across_requests(client) -> None:
    """The date is a build constant, not generated per request."""
    first = client.get("/fhir/metadata").json()
    second = client.get("/fhir/metadata").json()
    assert first["date"] == second["date"]


def test_metadata_validates_as_fhir_r4_capability_statement(client) -> None:
    """Full-payload validation with the fhir.resources library.

    fhir.resources >= 8 defaults to R5 and exposes R4-family models under
    the R4B subpackage; older 7.x releases expose R4B models at top level.
    Prefer the R4B model, fall back to the default, skip if not installed.
    """
    pytest.importorskip("fhir.resources")
    try:
        from fhir.resources.R4B.capabilitystatement import CapabilityStatement
    except ImportError:
        from fhir.resources.capabilitystatement import CapabilityStatement

    body = client.get("/fhir/metadata").json()
    statement = CapabilityStatement.model_validate(body)
    assert statement.fhirVersion == "4.0.1"


class TestEnforcedAuthExemption:
    """GET /fhir/metadata must stay reachable WITHOUT a token under real,
    enforced JWT validation — discovery happens pre-auth."""

    @pytest.fixture(scope="class")
    def enforced_hub(self, tmp_path_factory):
        from fastapi.testclient import TestClient

        tmp = str(tmp_path_factory.mktemp("hub_fhir_metadata_auth"))
        env = {
            "AUDIT_DB_PATH":   f"{tmp}/hub-audit.db",
            "ROOT_PATH":       "",
            "OPENMRS_URL":     "http://openmrs-auth-test:9997",
            "OPENELIS_URL":    "http://openelis-auth-test:9997",
            "ODOO_URL":        "http://odoo-auth-test:9997",
            "ODOO_DB":         "odoo",
            "POLL_INTERVAL_S": "99999",
        }
        with harness.isolated_service(
            "integration-hub", app_module="app.main", env=env
        ) as app:
            yield TestClient(app, raise_server_exceptions=False)

    def test_middleware_is_actually_enforced(self, enforced_hub):
        """Guard against a false pass: a non-exempt route must 401, proving
        the metadata 200 below comes from the exemption, not inert auth."""
        assert enforced_hub.get("/api/atomfeed/status").status_code == 401

    def test_metadata_returns_200_without_token(self, enforced_hub):
        resp = enforced_hub.get("/fhir/metadata")
        assert resp.status_code == 200, (
            "GET /fhir/metadata must be token-free under enforced auth, "
            f"got {resp.status_code}"
        )
        assert resp.headers["content-type"].startswith("application/fhir+json")
        assert resp.json()["resourceType"] == "CapabilityStatement"

    def test_only_metadata_is_exempt_under_fhir_prefix(self, enforced_hub):
        """The exemption is the exact /fhir/metadata path, not all of /fhir:
        any other (hypothetical future) /fhir route stays behind auth."""
        resp = enforced_hub.get("/fhir/Patient/some-id")
        assert resp.status_code == 401
