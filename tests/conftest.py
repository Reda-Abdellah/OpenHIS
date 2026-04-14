"""
Root test configuration — loaded by pytest before any test module.

Sets DEV_MODE and stub Keycloak env vars so that openhis_sdk.auth (and any
service jwt_auth.py that re-exports from it) always sees DEV_MODE=True on
first import.  Individual service conftests may override these via
monkeypatch or os.environ as needed.
"""
import os

os.environ.setdefault("DEV_MODE",                "true")
os.environ.setdefault("KEYCLOAK_URL",            "")
os.environ.setdefault("KEYCLOAK_REALM",          "openhis")
os.environ.setdefault("KEYCLOAK_TOKEN_URL",      "http://keycloak-test:8080/realms/openhis/protocol/openid-connect/token")
os.environ.setdefault("KEYCLOAK_CLIENT_ID",      "test-client")
os.environ.setdefault("KEYCLOAK_CLIENT_SECRET",  "test-secret")
