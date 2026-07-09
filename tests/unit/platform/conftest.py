"""
Shared fixtures for the platform (OPM) unit tests.

NOTE: this directory deliberately has NO __init__.py — as a package it would
be importable as `platform` and shadow the Python stdlib module of the same
name for the whole pytest session.

`opm` / `infra_render` are normally importable because `pip install -e
platform` puts the platform directory on sys.path; the explicit insert below
keeps the tests runnable even without the editable install.
"""
import sys
from pathlib import Path

import pytest

PLATFORM_DIR = Path(__file__).resolve().parents[3] / "platform"
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import opm  # noqa: E402

#: Env vars that `opm init` reads as secret sources — must be absent during
#: tests so the outcome doesn't depend on the developer's shell or the root
#: conftest (which sets e.g. KEYCLOAK_CLIENT_SECRET=test-secret).
_SECRET_ENV_VARS = list(opm._REQUIRED_SECRETS) + [
    "OPENHIS_POSTGRES_PASS",
    "OPENHIS_ADMIN_PASS",
    "OPENHIS_KEYCLOAK_PASS",
    "OPENHIS_KEYCLOAK_SECRET",
]


@pytest.fixture(autouse=True)
def clean_secret_env(monkeypatch):
    """Remove every secret-source env var before each test."""
    for var in _SECRET_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
