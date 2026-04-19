"""
Scenario 4 — Single Sign-On & Role-Based Access Control

Mirrors SCENARIO 4 in docs/verification_and_validation/v-and-v-scenario.md.

Setup: the conftest auto-provisions two service-account clients
  - `e2e-test-sa`   — all roles (admin/clinician/radiologist/lab-tech/...)
  - `e2e-noauth-sa` — default roles only (no admin/clinician/etc.)

Covers:
  ✅ S4.1 — admin_token carries every expected realm role
  ✅ S4.2 — admin_token is accepted by MPI /patients (role-gated endpoint)
  ✅ S4.3 — no-role token is rejected with 401/403 on a role-gated endpoint
  ✅ S4.4 — no token at all returns 401 everywhere
  ✅ S4.5 — malformed bearer returns 401 (not 200, not 500)
  ✅ S4.6 — Keycloak OIDC discovery document is reachable from the portal
"""
import pytest
import httpx


pytestmark = pytest.mark.e2e


PORTAL = "http://localhost"


def _decode_claims(jwt: str) -> dict:
    import base64, json
    body = jwt.split(".")[1]
    body += "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body.encode()))


class TestS4_TokenShape:

    def test_s4_1_admin_token_carries_expected_roles(self, admin_token):
        claims = _decode_claims(admin_token)
        roles = set(claims.get("roles", [])) | set(claims.get("realm_access", {}).get("roles", []))
        required = {"admin", "clinician", "radiologist", "lab-tech", "internal-sync"}
        missing = required - roles
        assert not missing, f"admin token missing roles {missing}; got {roles}"
        # Audience must include openhis-platform for service-side validators.
        aud = claims.get("aud", [])
        if isinstance(aud, str):
            aud = [aud]
        assert "openhis-platform" in aud, f"missing openhis-platform audience: {aud}"


class TestS4_AuthorizationOutcomes:

    def test_s4_2_admin_token_passes_mpi(self, mpi_api):
        r = mpi_api.get("/patients")
        assert r.status_code == 200

    def test_s4_3_no_role_token_forbidden_on_mpi(self, noauth_token):
        r = httpx.get(
            f"{PORTAL}/mpi/api/patients",
            headers={"Authorization": f"Bearer {noauth_token}"},
            timeout=10,
        )
        # 401 (auth failed due to missing claim/audience) or 403 (authed but
        # unauthorised) are both valid fail-closed outcomes. 200 is a fail-open
        # bug (previous OBJ 1.2); 500 would be a regression.
        assert r.status_code in (401, 403), r.text
        # Critical: must NOT succeed.
        assert r.status_code != 200

    def test_s4_4_missing_token_is_401(self):
        r = httpx.get(f"{PORTAL}/mpi/api/patients", timeout=5)
        assert r.status_code == 401

    def test_s4_5_malformed_token_is_401(self):
        r = httpx.get(
            f"{PORTAL}/mpi/api/patients",
            headers={"Authorization": "Bearer not.a.real.jwt"},
            timeout=5,
        )
        assert r.status_code == 401


class TestS4_KeycloakDiscovery:

    def test_s4_6_openid_configuration_reachable(self):
        r = httpx.get(
            f"{PORTAL}/keycloak/realms/openhis/.well-known/openid-configuration",
            timeout=5,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["issuer"].endswith("/realms/openhis")
        assert body["token_endpoint"].endswith("/protocol/openid-connect/token")
