"""
Root test configuration — loaded by pytest before any test module.

Sets DEV_MODE and stub Keycloak env vars so that openhis_sdk.auth (and any
service jwt_auth.py that re-exports from it) always sees DEV_MODE=True on
first import.  Individual service conftests may override these via
monkeypatch or os.environ as needed.

ENV=development is required: openhis_sdk.auth hard-exits at import time when
DEV_MODE=true is combined with any other ENV value (see T-04 / F#2, F#33).

The tests/auth suite is the exception to the DEV_MODE bypass: its fixtures
re-import the SDK and each service app with DEV_MODE=false and a mocked JWKS
so real 401/403/200 enforcement is exercised (see tests/auth/harness.py).
"""
import os

os.environ.setdefault("ENV",                     "development")
os.environ.setdefault("DEV_MODE",                "true")
# Non-empty stub: services treating KEYCLOAK_URL as required env must boot in
# tests; DEV_MODE=true keeps JWT validation bypassed regardless of the value.
os.environ.setdefault("KEYCLOAK_URL",            "http://keycloak-test:8080/keycloak")
os.environ.setdefault("KEYCLOAK_REALM",          "openhis")
os.environ.setdefault("KEYCLOAK_TOKEN_URL",      "http://keycloak-test:8080/realms/openhis/protocol/openid-connect/token")
os.environ.setdefault("KEYCLOAK_CLIENT_ID",      "test-client")
os.environ.setdefault("KEYCLOAK_CLIENT_SECRET",  "test-secret")
