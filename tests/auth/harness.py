"""
Real-auth test harness for OpenHIS services (T-04).

The rest of the test tree runs with DEV_MODE=true, which short-circuits every
JWT code path. This module lets a test boot any native service's FastAPI app
in-process with **real auth enforced** and mint RS256 tokens that validate
against an in-memory JWKS — no Keycloak, no network.

How it works
------------
* A session-unique RSA keypair is generated at import time. Its public half
  is exposed as ``TEST_JWKS`` (the JWK set "Keycloak" would serve).
* ``boot_service_app`` / ``isolated_service`` set ``DEV_MODE=false`` and a
  dummy ``KEYCLOAK_URL``, purge the SDK + service modules from
  ``sys.modules`` (their module-level constants are read at import time),
  re-import the service app, then prime ``openhis_sdk.auth._JWKS_CACHE``
  with ``TEST_JWKS`` so the JWKS "fetch" never touches the network.
* ``make_token(roles=[...])`` signs an RS256 JWT with the harness key; the
  SDK validates it exactly as it would a real Keycloak token (signature,
  ``exp``, ``aud``). ``make_foreign_token`` signs with a key *absent* from
  ``TEST_JWKS`` and must always be rejected.

Usage from any test (tests/auth/ is on sys.path while its suite runs)::

    import harness
    from fastapi.testclient import TestClient

    def test_admin_config_requires_admin_token(tmp_path):
        env = {"DB_PATH": f"{tmp_path}/admin.db", "ROOT_PATH": ""}
        with harness.isolated_service("admin", env=env, init_db=True) as app:
            client = TestClient(app, raise_server_exceptions=False)
            assert client.get("/api/config").status_code == 401
            ok = client.get("/api/config", headers=harness.auth_header(["admin"]))
            assert ok.status_code == 200

State hygiene: ``isolated_service`` snapshots ``os.environ`` and ``sys.path``
and purges the imported service/SDK modules again on exit, so suites running
after tests/auth in the same pytest invocation still see the DEV_MODE=true
world they expect.
"""
from __future__ import annotations

import base64
import importlib
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"

# .invalid is a reserved TLD — an accidental real JWKS fetch fails fast on DNS.
TEST_KEYCLOAK_URL = "http://keycloak-auth-harness.invalid"
TEST_REALM = "openhis"
TEST_AUDIENCE = "openhis-platform"
TEST_KID = "auth-harness-signing-key"
_FOREIGN_KID = "auth-harness-foreign-key"


def _generate_key() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return key, pem


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8 or 1, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _public_jwk(key: rsa.RSAPrivateKey, kid: str) -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }


_SIGNING_KEY, _SIGNING_PEM = _generate_key()
_FOREIGN_KEY, _FOREIGN_PEM = _generate_key()

#: The JWK set the mocked "Keycloak" serves. Only the signing key is in it,
#: so tokens minted with make_foreign_token() must fail signature validation.
TEST_JWKS: dict = {"keys": [_public_jwk(_SIGNING_KEY, TEST_KID)]}


def make_token(
    roles: tuple[str, ...] | list[str] = (),
    *,
    username: str = "auth-harness",
    audience: str = TEST_AUDIENCE,
    expires_in_s: int = 300,
    _pem: Optional[str] = None,
    _kid: Optional[str] = None,
    **extra_claims,
) -> str:
    """Mint an RS256 JWT carrying ``roles`` that validates against TEST_JWKS.

    Pass ``expires_in_s=-60`` for an already-expired token, or override
    ``audience`` to exercise audience-mismatch rejection.
    """
    now = datetime.now(timezone.utc)
    claims = {
        "sub": f"auth-harness-{username}",
        "preferred_username": username,
        "roles": list(roles),
        "aud": audience,
        "iss": f"{TEST_KEYCLOAK_URL}/realms/{TEST_REALM}",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_s)).timestamp()),
    }
    claims.update(extra_claims)
    return jose_jwt.encode(
        claims,
        _pem or _SIGNING_PEM,
        algorithm="RS256",
        headers={"kid": _kid or TEST_KID},
    )


def make_foreign_token(roles: tuple[str, ...] | list[str] = (), **kwargs) -> str:
    """Mint a token signed by a key NOT in TEST_JWKS — must always be rejected."""
    return make_token(roles, _pem=_FOREIGN_PEM, _kid=_FOREIGN_KID, **kwargs)


def auth_header(roles: tuple[str, ...] | list[str] = (), **kwargs) -> dict[str, str]:
    """Authorization header carrying a valid harness token with ``roles``."""
    return {"Authorization": f"Bearer {make_token(roles, **kwargs)}"}


# ─────────────────────────── app loading ────────────────────────────────────
# Top-level module names that native services define. They live in flat
# per-service directories, so they collide across services and with each
# other; purge them before importing a different service (same pattern as
# tests/integration/conftest.py).
_SERVICE_TOP_LEVEL_MODULES = {
    "main", "database", "jwt_auth", "log_config", "security",
    "bus_consumer", "collector", "scheduler",
    "handlers", "mllp", "parser", "builder",
    "openmrs_sync", "orthanc_client", "runner", "dicom_factory", "presets",
    "auth", "proxy", "svc_token",
}
_SERVICE_MODULE_PREFIXES = ("routers", "translators", "app")


def purge_service_modules() -> None:
    """Drop every imported service module so the next import is fresh."""
    for name in list(sys.modules):
        top = name.split(".", 1)[0]
        if top in _SERVICE_TOP_LEVEL_MODULES or top in _SERVICE_MODULE_PREFIXES:
            del sys.modules[name]


def purge_sdk_modules() -> None:
    """Drop openhis_sdk so DEV_MODE/KEYCLOAK_* are re-read on next import."""
    for name in list(sys.modules):
        if name == "openhis_sdk" or name.startswith("openhis_sdk."):
            del sys.modules[name]


def enforced_auth_env() -> dict[str, str]:
    """Env vars that turn real JWT validation ON, wired to the harness JWKS."""
    return {
        "DEV_MODE": "false",
        "KEYCLOAK_URL": TEST_KEYCLOAK_URL,
        "KEYCLOAK_REALM": TEST_REALM,
        "KEYCLOAK_CLIENT_ID": TEST_AUDIENCE,
        "KEYCLOAK_AUDIENCE": TEST_AUDIENCE,
    }


def prime_jwks_cache() -> None:
    """Inject TEST_JWKS into the SDK's JWKS cache — no network fetch happens."""
    auth_module = importlib.import_module("openhis_sdk.auth")
    auth_module._JWKS_CACHE = TEST_JWKS
    auth_module._JWKS_FETCHED_AT = time.monotonic()


def boot_service_app(
    service_dir: str,
    *,
    app_module: str = "main",
    env: Optional[dict[str, str]] = None,
    init_db: bool = False,
):
    """Import ``services/<service_dir>``'s FastAPI app with real auth enforced.

    Mutates os.environ, sys.path and sys.modules — use ``isolated_service``
    (or snapshot/restore yourself) so later suites are unaffected.
    """
    svc_path = str(SERVICES_ROOT / service_dir)
    services_root = str(SERVICES_ROOT)
    # Scrub other services' paths; several ship an `app/` package or a flat
    # `main.py` that would shadow this service's modules.
    sys.path[:] = [p for p in sys.path
                   if not (p.startswith(services_root) and p != svc_path)]
    if svc_path in sys.path:
        sys.path.remove(svc_path)
    sys.path.insert(0, svc_path)

    for key, value in {**enforced_auth_env(), **(env or {})}.items():
        os.environ[key] = value

    purge_sdk_modules()
    purge_service_modules()

    module = importlib.import_module(app_module)
    if init_db:
        database = importlib.import_module("database")
        database.init_db()
    prime_jwks_cache()
    return module.app


@contextmanager
def isolated_service(
    service_dir: str,
    *,
    app_module: str = "main",
    env: Optional[dict[str, str]] = None,
    init_db: bool = False,
) -> Iterator:
    """Context manager around ``boot_service_app`` with full state restore."""
    saved_environ = dict(os.environ)
    saved_path = list(sys.path)
    try:
        yield boot_service_app(
            service_dir, app_module=app_module, env=env, init_db=init_db
        )
    finally:
        purge_service_modules()
        purge_sdk_modules()
        sys.path[:] = saved_path
        os.environ.clear()
        os.environ.update(saved_environ)


# ─────────────────────────── service catalog ────────────────────────────────

@dataclass(frozen=True)
class RoleGate:
    """A route guarded by require_roles — used for the 403 wrong-role case."""
    path: str
    method: str = "get"
    denied_roles: tuple[str, ...] = ("nurse",)


@dataclass(frozen=True)
class ServiceSpec:
    """Everything the deny-by-default suite needs to probe one service.

    authorized_statuses semantics for the valid-token case:
      * tuple of ints — response status must be one of them (normal case);
      * None  — only assert the auth layers passed (status not in 401/403);
                used when the handler needs infra unavailable in unit runs
                (e.g. MPI's PostgreSQL);
      * ()    — skip the valid-token case entirely (e.g. patient-portal,
                whose /api/me sits behind its own session layer that returns
                401 even for a valid platform JWT).
    """
    name: str
    service_dir: str
    protected_path: str
    method: str = "get"
    app_module: str = "main"
    init_db: bool = True
    env: Callable[[str], dict[str, str]] = lambda tmp: {}
    granted_roles: tuple[str, ...] = ("admin",)
    authorized_statuses: Optional[tuple[int, ...]] = (200,)
    role_gate: Optional[RoleGate] = None
    public_paths: tuple[str, ...] = ("/docs",)


SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        name="admin",
        service_dir="admin",
        protected_path="/api/config",            # require_token dependency
        env=lambda tmp: {
            "DB_PATH": f"{tmp}/admin.db",
            "ROOT_PATH": "",
            "LOG_FORMAT": "text",
            "REDIS_URL": "",
        },
        granted_roles=("admin",),
        # GET /api/identity/users/{id} requires the admin role; the dep fires
        # before the handler, so no Keycloak admin API call is made on 403.
        role_gate=RoleGate(path="/api/identity/users/auth-harness-user"),
        public_paths=("/api/health", "/docs"),
    ),
    ServiceSpec(
        name="ai-controller",
        service_dir="ai-controller",
        protected_path="/api/pipelines",         # middleware + require_roles (T-02)
        env=lambda tmp: {
            "DB_PATH": f"{tmp}/ai.db",
            "ROOT_PATH": "",
            "ORTHANC_URL": "http://localhost:19999",
            "JOBS_DATA_DIR": f"{tmp}/ai_jobs",
            "FHIR_BRIDGE_URL": "",
            "REDIS_URL": "",
            "OPENELIS_URL": "",
        },
        granted_roles=("admin",),
        # GET /api/pipelines requires admin|radiologist (T-02 role gates).
        role_gate=RoleGate(path="/api/pipelines"),
        public_paths=("/api/health", "/docs"),
    ),
    ServiceSpec(
        name="analytics",
        service_dir="analytics",
        protected_path="/api/metrics/summary",   # middleware + require_roles
        env=lambda tmp: {
            "DB_PATH": f"{tmp}/analytics.db",
            "ROOT_PATH": "",
            "OPENMRS_URL": "http://localhost:19999",
            "OPENMRS_USER": "admin",
            "OPENMRS_PASS": "x",
            "OPENELIS_URL": "http://localhost:19999",
            "RIS_URL": "http://localhost:19999/api",
            "AI_CONTROLLER_URL": "http://localhost:19999/api",
            "COLLECT_INTERVAL_MIN": "99999",
        },
        granted_roles=("clinician",),
        role_gate=RoleGate(path="/api/metrics/summary"),
        public_paths=("/api/health", "/docs"),
    ),
    ServiceSpec(
        name="hl7",
        service_dir="hl7",
        protected_path="/api/messages",          # JWTMiddleware only
        env=lambda tmp: {
            "DB_PATH": f"{tmp}/hl7.db",
            "ROOT_PATH": "",
            "MLLP_ENABLED": "false",
            "OPENMRS_URL": "http://localhost:19999",
            "OPENMRS_USER": "admin",
            "OPENMRS_PASS": "x",
        },
        granted_roles=("clinician",),
        public_paths=("/api/health", "/docs"),
    ),
    ServiceSpec(
        name="integration-hub",
        service_dir="integration-hub",
        app_module="app.main",
        init_db=False,
        protected_path="/api/atomfeed/status",   # JWTMiddleware (token-only by design)
        env=lambda tmp: {
            "AUDIT_DB_PATH": f"{tmp}/hub-audit.db",
            "ROOT_PATH": "",
            "OPENMRS_URL": "http://openmrs-auth-test:9997",
            "OPENELIS_URL": "http://openelis-auth-test:9997",
            "ODOO_URL": "http://odoo-auth-test:9997",
            "ODOO_DB": "odoo",
            "POLL_INTERVAL_S": "99999",
        },
        granted_roles=("admin",),
        # T-06 role gates: POST /api/atomfeed/trigger requires admin (the
        # /api/events/* and /api/test/route-order gates are covered in depth
        # by tests/unit/integration-hub/test_hub_auth_gates.py).
        role_gate=RoleGate(path="/api/atomfeed/trigger", method="post"),
        # /api/health fans out to upstream health checks — slow without the
        # stack, so only /docs is asserted as public here.
        public_paths=("/docs",),
    ),
    ServiceSpec(
        name="mpi",
        service_dir="mpi",
        init_db=False,                           # PostgreSQL-bound (see tests/unit/mpi)
        protected_path="/api/patients",          # middleware + require_roles
        env=lambda tmp: {"ROOT_PATH": ""},
        granted_roles=("clinician",),
        authorized_statuses=None,                # handler needs Postgres; auth layers must pass
        role_gate=RoleGate(path="/api/patients"),
        public_paths=("/docs",),                 # /api/health needs Postgres
    ),
    ServiceSpec(
        name="mpi-lookup",
        service_dir="mpi",
        init_db=False,                           # PostgreSQL-bound (see tests/unit/mpi)
        protected_path="/api/patients/lookup",   # middleware + require_roles (T-05)
        env=lambda tmp: {"ROOT_PATH": ""},
        granted_roles=("lab-tech",),
        authorized_statuses=None,                # handler needs Postgres; auth layers must pass
        role_gate=RoleGate(path="/api/patients/lookup"),
        public_paths=("/docs",),                 # /api/health needs Postgres
    ),
    ServiceSpec(
        name="mpi-matching",
        service_dir="mpi",
        init_db=False,                           # PostgreSQL-bound (see tests/unit/mpi)
        protected_path="/api/matching/candidates",  # middleware + require_roles (T-05)
        env=lambda tmp: {"ROOT_PATH": ""},
        granted_roles=("clinician",),
        authorized_statuses=None,                # handler needs Postgres; auth layers must pass
        # Identity-stewardship route: even read-only clinical roles are denied.
        role_gate=RoleGate(
            path="/api/matching/candidates",
            denied_roles=("radiologist", "lab-tech"),
        ),
        public_paths=("/docs",),                 # /api/health needs Postgres
    ),
    ServiceSpec(
        name="patient-portal",
        service_dir="patient-portal",
        protected_path="/api/me",                # JWTMiddleware; session auth beneath
        env=lambda tmp: {"DB_PATH": f"{tmp}/portal.db", "ROOT_PATH": ""},
        granted_roles=("clinician",),
        authorized_statuses=(),                  # session layer 401s even with a valid JWT
        public_paths=("/api/health", "/docs"),
    ),
    ServiceSpec(
        name="ris",
        service_dir="ris",
        protected_path="/api/orders",            # middleware + require_roles
        env=lambda tmp: {
            "DB_PATH": f"{tmp}/ris.db",
            "ROOT_PATH": "",
            "OPENMRS_URL": "http://localhost:19999",
            "OPENMRS_USER": "admin",
            "OPENMRS_PASS": "admin",
            "POLL_INTERVAL_S": "99999",
        },
        granted_roles=("radiologist",),
        role_gate=RoleGate(path="/api/orders"),
        public_paths=("/api/health", "/api/auth/config", "/docs"),
    ),
    ServiceSpec(
        name="ris-patients",
        service_dir="ris",
        protected_path="/api/patients",          # middleware + require_roles (T-05)
        env=lambda tmp: {
            "DB_PATH": f"{tmp}/ris.db",
            "ROOT_PATH": "",
            "OPENMRS_URL": "http://localhost:19999",
            "OPENMRS_USER": "admin",
            "OPENMRS_PASS": "admin",
            "POLL_INTERVAL_S": "99999",
        },
        granted_roles=("lab-tech",),
        role_gate=RoleGate(path="/api/patients"),
        public_paths=("/api/health", "/api/auth/config", "/docs"),
    ),
    ServiceSpec(
        name="simulator",
        service_dir="simulator",
        init_db=False,                           # no database module
        protected_path="/api/jobs",              # JWTMiddleware only
        env=lambda tmp: {
            "ROOT_PATH": "",
            "ORTHANC_URL": "http://orthanc-auth-test:9997",
        },
        granted_roles=("radiologist",),
        # T-06: POST /api/generate requires admin|radiologist. The role dep
        # fires before body validation, so no payload is needed for the 403.
        role_gate=RoleGate(path="/api/generate", method="post"),
        public_paths=("/api/health", "/docs"),
    ),
)
