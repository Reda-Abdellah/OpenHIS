"""
openhis_sdk.auth DEV_MODE guard (T-04, F#2).

The rule is "require ENV=development", not "forbid ENV=production":
DEV_MODE=true with any other ENV value — staging, production, or unset —
must hard-exit at import time so a JWT bypass can never reach a deployed
environment silently.
"""
import importlib
import sys

import pytest


def _purge_sdk() -> None:
    for name in [m for m in sys.modules
                 if m == "openhis_sdk" or m.startswith("openhis_sdk.")]:
        del sys.modules[name]


@pytest.fixture
def fresh_sdk_auth(monkeypatch):
    """Factory: re-import openhis_sdk.auth under a controlled env.

    Purges the SDK afterwards so later tests re-import it under the
    DEV_MODE=true / ENV=development world set by tests/conftest.py.
    """
    def _import(dev_mode: str, env: str | None):
        _purge_sdk()
        monkeypatch.setenv("DEV_MODE", dev_mode)
        if env is None:
            monkeypatch.delenv("ENV", raising=False)
        else:
            monkeypatch.setenv("ENV", env)
        return importlib.import_module("openhis_sdk.auth")

    yield _import
    _purge_sdk()


def test_dev_mode_with_staging_env_exits(fresh_sdk_auth):
    with pytest.raises(SystemExit):
        fresh_sdk_auth("true", "staging")


def test_dev_mode_with_production_env_exits(fresh_sdk_auth):
    with pytest.raises(SystemExit):
        fresh_sdk_auth("true", "production")


def test_dev_mode_with_unset_env_exits(fresh_sdk_auth):
    with pytest.raises(SystemExit):
        fresh_sdk_auth("true", None)


def test_dev_mode_allowed_when_env_is_development(fresh_sdk_auth):
    module = fresh_sdk_auth("true", "development")
    assert module.DEV_MODE is True


def test_validation_on_by_default_in_any_env(fresh_sdk_auth):
    module = fresh_sdk_auth("false", "production")
    assert module.DEV_MODE is False
