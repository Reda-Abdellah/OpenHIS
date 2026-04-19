"""
End-to-end test configuration — live-stack scenarios.

These tests walk the Verification & Validation scenarios against a running
platform (`make up`). They are opt-in:

    pytest tests/e2e --e2e
    OPENHIS_E2E=1 pytest tests/e2e
    make e2e

Shared fixtures:
- `admin_token`     — JWT with admin + all clinical roles, acquired from Keycloak
                      via the e2e-test-sa service-account client (auto-provisioned
                      on first run using the Keycloak master admin credentials).
- `noauth_token`    — JWT from a service account with zero roles (for RBAC tests).
- `auth_hdrs`       — {"Authorization": f"Bearer {admin_token}"} convenience.
- `http`            — preconfigured httpx.Client keyed on the portal URL.
- `admin_api`       — httpx.Client rooted at /admin/api.
- `mpi_api`         — httpx.Client rooted at /mpi/api.
- `hub_api`         — httpx.Client rooted at /integration-hub/api.
- `hl7_api`         — httpx.Client rooted at /hl7/api.
- `ris_api`         — httpx.Client rooted at /ris/api.
- `orthanc`         — httpx.Client rooted at /orthanc (no auth required).
- `simulator_api`   — httpx.Client rooted at /simulator/api.
- `ai_api`          — httpx.Client rooted at /ai-controller/api.
- `docker_available`— True if `docker ps` succeeds without sudo (gates Scenario 6).

Cleanup: every fixture that creates platform state tags it with the `E2E-` MRN
prefix or `e2e-test` label so `_cleanup_e2e_data()` can remove it at session end.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from typing import Iterator

import httpx
import pytest


# ── Configuration ───────────────────────────────────────────────────────────

PORTAL          = os.getenv("OPENHIS_PORTAL_URL",   "http://localhost")
KEYCLOAK_ADMIN  = os.getenv("KEYCLOAK_MASTER_USER", "admin")
KEYCLOAK_PASS   = os.getenv("KEYCLOAK_MASTER_PASS", "admin")
REALM           = os.getenv("KEYCLOAK_REALM",       "openhis")
E2E_MRN_PREFIX  = "E2E-"
E2E_SA_CLIENT   = "e2e-test-sa"
E2E_NOAUTH_SA   = "e2e-noauth-sa"   # zero-roles SA for RBAC tests


# ── Pytest plumbing ─────────────────────────────────────────────────────────

def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--e2e", action="store_true", default=False,
        help="Run end-to-end V&V scenarios against a live stack",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "e2e: marks tests as end-to-end scenarios (require live stack)"
    )
    config.addinivalue_line(
        "markers", "resilience: requires docker socket access (Scenario 6)"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if config.getoption("--e2e") or os.getenv("OPENHIS_E2E"):
        return
    skip = pytest.mark.skip(reason="e2e tests require --e2e or OPENHIS_E2E=1")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


# ── Live-stack precondition ─────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _live_stack() -> None:
    """Skip the entire e2e suite if the portal is not reachable."""
    try:
        r = httpx.get(f"{PORTAL}/health", timeout=3)
        assert r.status_code == 200
    except Exception as e:
        pytest.skip(f"Live stack not reachable at {PORTAL}: {e}", allow_module_level=True)


# ── Keycloak provisioning ───────────────────────────────────────────────────

def _master_admin_token() -> str:
    r = httpx.post(
        f"{PORTAL}/keycloak/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id":  "admin-cli",
            "username":   KEYCLOAK_ADMIN,
            "password":   KEYCLOAK_PASS,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _ensure_sa_client(admin_tok: str, client_id: str, roles: list[str]) -> str:
    """
    Idempotently create a confidential service-account client with the given
    realm roles, the audience mapper for `openhis-platform`, and the realm-
    roles mapper. Returns the client's secret.
    """
    base = f"{PORTAL}/keycloak/admin/realms/{REALM}"
    hdrs = {"Authorization": f"Bearer {admin_tok}"}

    r = httpx.get(f"{base}/clients", params={"clientId": client_id}, headers=hdrs, timeout=10)
    r.raise_for_status()
    found = r.json()

    if not found:
        create = httpx.post(
            f"{base}/clients",
            headers={**hdrs, "Content-Type": "application/json"},
            json={
                "clientId":                   client_id,
                "enabled":                    True,
                "publicClient":               False,
                "serviceAccountsEnabled":     True,
                "directAccessGrantsEnabled":  False,
                "standardFlowEnabled":        False,
                "implicitFlowEnabled":        False,
                "protocol":                   "openid-connect",
                "protocolMappers": [
                    {
                        "name":           "realm-roles",
                        "protocol":       "openid-connect",
                        "protocolMapper": "oidc-usermodel-realm-role-mapper",
                        "config": {
                            "claim.name":          "roles",
                            "jsonType.label":      "String",
                            "multivalued":         "true",
                            "id.token.claim":      "true",
                            "access.token.claim":  "true",
                            "userinfo.token.claim":"true",
                        },
                    },
                    {
                        "name":           "openhis-platform-audience",
                        "protocol":       "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.custom.audience": "openhis-platform",
                            "id.token.claim":           "false",
                            "access.token.claim":       "true",
                        },
                    },
                ],
            },
            timeout=10,
        )
        create.raise_for_status()
        r = httpx.get(f"{base}/clients", params={"clientId": client_id}, headers=hdrs, timeout=10)
        r.raise_for_status()
        found = r.json()

    client_uuid = found[0]["id"]

    # Assign realm roles to the client's service-account user (idempotent).
    sa_user_id = httpx.get(
        f"{base}/clients/{client_uuid}/service-account-user",
        headers=hdrs, timeout=10,
    ).json()["id"]

    existing_roles = {
        r_["name"]
        for r_ in httpx.get(
            f"{base}/users/{sa_user_id}/role-mappings/realm",
            headers=hdrs, timeout=10,
        ).json()
    }
    want = set(roles) - existing_roles
    if want:
        all_roles = httpx.get(f"{base}/roles", headers=hdrs, timeout=10).json()
        assign = [{"id": r_["id"], "name": r_["name"]} for r_ in all_roles if r_["name"] in want]
        if assign:
            r2 = httpx.post(
                f"{base}/users/{sa_user_id}/role-mappings/realm",
                headers={**hdrs, "Content-Type": "application/json"},
                json=assign,
                timeout=10,
            )
            r2.raise_for_status()

    secret = httpx.get(
        f"{base}/clients/{client_uuid}/client-secret",
        headers=hdrs, timeout=10,
    ).json()["value"]
    return secret


def _sa_token(client_id: str, secret: str) -> str:
    r = httpx.post(
        f"{PORTAL}/keycloak/realms/{REALM}/protocol/openid-connect/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     client_id,
            "client_secret": secret,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


# ── Token fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def admin_token() -> str:
    """
    Service-account JWT with every clinical role + admin. Created on first run
    and reused across sessions. Requires the Keycloak master admin password
    (default `admin/admin` in dev; override via KEYCLOAK_MASTER_PASS env var).
    """
    try:
        master = _master_admin_token()
    except Exception as e:
        pytest.skip(f"Cannot reach Keycloak master realm: {e}")

    secret = _ensure_sa_client(
        master,
        E2E_SA_CLIENT,
        roles=["admin", "clinician", "radiologist", "lab-tech",
               "pharmacist", "patient", "internal-sync"],
    )
    return _sa_token(E2E_SA_CLIENT, secret)


@pytest.fixture(scope="session")
def noauth_token() -> str:
    """
    Service-account JWT with zero additional realm roles. Used by RBAC tests
    to assert that protected endpoints return 403 for a valid-but-unauthorised
    caller (distinct from a missing-token 401).
    """
    try:
        master = _master_admin_token()
    except Exception as e:
        pytest.skip(f"Cannot reach Keycloak master realm: {e}")
    secret = _ensure_sa_client(master, E2E_NOAUTH_SA, roles=[])
    return _sa_token(E2E_NOAUTH_SA, secret)


@pytest.fixture
def auth_hdrs(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# ── HTTP clients ────────────────────────────────────────────────────────────

def _client(base_path: str, token: str | None = None) -> httpx.Client:
    hdrs = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=f"{PORTAL}{base_path}", headers=hdrs, timeout=15)


@pytest.fixture
def http(admin_token: str) -> Iterator[httpx.Client]:
    with _client("", admin_token) as c:
        yield c


@pytest.fixture
def admin_api(admin_token: str) -> Iterator[httpx.Client]:
    with _client("/admin/api", admin_token) as c:
        yield c


@pytest.fixture
def mpi_api(admin_token: str) -> Iterator[httpx.Client]:
    with _client("/mpi/api", admin_token) as c:
        yield c


@pytest.fixture
def hub_api(admin_token: str) -> Iterator[httpx.Client]:
    with _client("/integration-hub/api", admin_token) as c:
        yield c


@pytest.fixture
def hl7_api(admin_token: str) -> Iterator[httpx.Client]:
    with _client("/hl7/api", admin_token) as c:
        yield c


@pytest.fixture
def ris_api(admin_token: str) -> Iterator[httpx.Client]:
    with _client("/ris/api", admin_token) as c:
        yield c


@pytest.fixture
def analytics_api(admin_token: str) -> Iterator[httpx.Client]:
    with _client("/analytics/api", admin_token) as c:
        yield c


@pytest.fixture
def simulator_api(admin_token: str) -> Iterator[httpx.Client]:
    with _client("/simulator/api", admin_token) as c:
        yield c


@pytest.fixture
def ai_api(admin_token: str) -> Iterator[httpx.Client]:
    with _client("/ai-controller/api", admin_token) as c:
        yield c


@pytest.fixture
def orthanc() -> Iterator[httpx.Client]:
    """Orthanc REST API is not behind Keycloak in the default stack."""
    with _client("/orthanc") as c:
        yield c


@pytest.fixture
def portal_api() -> Iterator[httpx.Client]:
    """
    Patient-portal HTTP client. Intentionally no Authorization header — the
    portal owns its own session model (POST /api/auth/login → opaque session
    UUID, validated in-process) and the global JWTMiddleware skips
    /api/health and /api/auth/* on this service.
    """
    with _client("/patient-portal/api") as c:
        yield c


# ── Test-data helpers ───────────────────────────────────────────────────────

@pytest.fixture
def fresh_mrn() -> str:
    """Return a unique MRN tagged `E2E-<hex>` — caught by the session cleanup."""
    return f"{E2E_MRN_PREFIX}{uuid.uuid4().hex[:10].upper()}"


@pytest.fixture(scope="session", autouse=True)
def _cleanup_e2e_data(request) -> Iterator[None]:
    """
    Session teardown notice.

    MPI has no DELETE route on /api/patients (see `services/mpi/routers/patients.py`),
    so true cleanup isn't possible from the API. Every `fresh_mrn` is UUID-scoped
    and unique, so repeat runs never collide. This fixture only reports leftover
    `E2E-*` rows so the developer can decide whether to truncate the DB manually.
    """
    yield
    try:
        master = _master_admin_token()
        secret = _ensure_sa_client(
            master, E2E_SA_CLIENT,
            roles=["admin", "clinician", "radiologist", "lab-tech",
                   "pharmacist", "patient", "internal-sync"],
        )
        tok = _sa_token(E2E_SA_CLIENT, secret)
        r = httpx.get(
            f"{PORTAL}/mpi/api/patients",
            headers={"Authorization": f"Bearer {tok}"}, timeout=10,
        )
        if r.status_code == 200:
            leftover = [p["mrn"] for p in r.json() if p.get("mrn", "").startswith(E2E_MRN_PREFIX)]
            if leftover:
                terminal = request.config.pluginmanager.get_plugin("terminalreporter")
                if terminal:
                    terminal.write_line(
                        f"\n[e2e cleanup] {len(leftover)} leftover master_patients "
                        f"tagged {E2E_MRN_PREFIX}* remain in MPI (no DELETE route available).",
                    )
    except Exception:
        pass


# ── Event-stream helpers ────────────────────────────────────────────────────

def wait_for_event(
    admin_client: httpx.Client,
    event_type: str,
    *,
    timeout: float = 10.0,
    since_id: str | None = None,
) -> dict | None:
    """
    Poll `/admin/api/events/recent` until an event matching `event_type` appears
    (optionally strictly newer than `since_id`), or the timeout elapses.
    Returns the event dict, or None if no match in time.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = admin_client.get("/events/recent", params={"limit": 200})
        if r.status_code == 200:
            for ev in r.json():
                if ev.get("type") != event_type:
                    continue
                if since_id is not None and ev.get("id", "") <= since_id:
                    continue
                return ev
        time.sleep(0.5)
    return None


def latest_event_id(admin_client: httpx.Client) -> str:
    """Return the Redis Stream ID of the newest event, or '0-0' if stream empty."""
    r = admin_client.get("/events/recent", params={"limit": 1})
    if r.status_code == 200 and r.json():
        return r.json()[-1].get("id", "0-0")
    return "0-0"


# ── Docker availability (Scenario 6) ────────────────────────────────────────

@pytest.fixture(scope="session")
def docker_available() -> bool:
    """True iff `docker ps` works without sudo. Gates resilience scenarios."""
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False
