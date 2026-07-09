"""
Unit tests for infra/orthanc/plugin.py — Keycloak client-credentials token.

The plugin runs inside Orthanc's embedded Python and imports the `orthanc`
module at import time, so tests stub it in sys.modules and load the plugin
straight from its file path with importlib.

Covers:
- auth disabled when ORTHANC_KC_TOKEN_URL is unset (back-compat behaviour)
- token fetched once and cached across calls
- expiry-aware refresh (short expires_in forces a refetch)
- fail-soft: Keycloak down → warning logged, POST still goes out bare
- bearer header attached to outgoing notifications
- 401 from a target → cached token discarded, retried once with a fresh one
"""
import importlib.util
import json
import sys
import types
import urllib.error
from pathlib import Path
from typing import Callable, Optional

import pytest

PLUGIN_PATH = (
    Path(__file__).resolve().parents[3] / "infra" / "orthanc" / "plugin.py"
)
TOKEN_URL = "http://keycloak:8080/keycloak/realms/openhis/protocol/openid-connect/token"
BASE_ENV = {
    "AI_CONTROLLER_URL": "http://ai-controller:8000",
    "ORTHANC_KC_TOKEN_URL": TOKEN_URL,
    "ORTHANC_KC_CLIENT_ID": "orthanc-sa",
    "ORTHANC_KC_CLIENT_SECRET": "unit-test-secret",
}
_PLUGIN_ENV_KEYS = (
    "AI_CONTROLLER_URL",
    "FHIR_BRIDGE_URL",
    "ORTHANC_KC_TOKEN_URL",
    "ORTHANC_KC_CLIENT_ID",
    "ORTHANC_KC_CLIENT_SECRET",
)


class _OrthancStub(types.ModuleType):
    """Minimal stand-in for Orthanc's embedded `orthanc` module."""

    def __init__(self) -> None:
        super().__init__("orthanc")
        self.infos: list = []
        self.warnings: list = []
        self.errors: list = []
        self.callback = None

    def LogInfo(self, msg: str) -> None:      # noqa: N802 — Orthanc API name
        self.infos.append(msg)

    def LogWarning(self, msg: str) -> None:   # noqa: N802
        self.warnings.append(msg)

    def LogError(self, msg: str) -> None:     # noqa: N802
        self.errors.append(msg)

    def RegisterOnStoredInstanceCallback(self, cb) -> None:  # noqa: N802
        self.callback = cb


class _Resp:
    """Context-manager response mimicking urllib's addinfourl."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _load_plugin(monkeypatch, env: dict):
    """Import plugin.py fresh with *env* and a stubbed orthanc module."""
    stub = _OrthancStub()
    monkeypatch.setitem(sys.modules, "orthanc", stub)
    for key in _PLUGIN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location(
        "orthanc_plugin_under_test", PLUGIN_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, stub


def _wire_urlopen(
    monkeypatch,
    mod,
    token_handler: Optional[Callable[[int], object]] = None,
    post_handler: Optional[Callable[[object, int], object]] = None,
):
    """Replace urlopen with a dispatcher keyed on the request URL."""
    calls = {"token": 0, "posts": []}

    def fake_urlopen(req, timeout=None):
        if req.full_url == TOKEN_URL:
            calls["token"] += 1
            result = token_handler(calls["token"])
            if isinstance(result, Exception):
                raise result
            return _Resp(json.dumps(result).encode())
        calls["posts"].append(req)
        result = post_handler(req, len(calls["posts"]))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    return calls


def _token_payload(token: str, expires_in: int = 300) -> dict:
    return {"access_token": token, "expires_in": expires_in}


# ── auth disabled (no token URL) ───────────────────────────────────────────────

def test_no_token_url_disables_auth(monkeypatch):
    env = {k: v for k, v in BASE_ENV.items() if k != "ORTHANC_KC_TOKEN_URL"}
    mod, stub = _load_plugin(monkeypatch, env)
    calls = _wire_urlopen(
        monkeypatch, mod,
        token_handler=lambda n: pytest.fail("token endpoint must not be called"),
        post_handler=lambda req, n: _Resp(b"{}"),
    )

    assert mod._get_token() is None
    mod._post_json("http://ai-controller:8000/api/trigger-instance", {"instance_id": "i1"})

    assert calls["token"] == 0
    assert len(calls["posts"]) == 1
    assert calls["posts"][0].get_header("Authorization") is None


# ── caching & expiry ───────────────────────────────────────────────────────────

def test_token_fetched_once_and_cached(monkeypatch):
    mod, stub = _load_plugin(monkeypatch, BASE_ENV)
    calls = _wire_urlopen(
        monkeypatch, mod, token_handler=lambda n: _token_payload(f"tok{n}")
    )

    assert mod._get_token() == "tok1"
    assert mod._get_token() == "tok1"
    assert calls["token"] == 1


def test_short_expiry_forces_refresh(monkeypatch):
    mod, stub = _load_plugin(monkeypatch, BASE_ENV)
    # expires_in below the 60 s skew → cache is never considered fresh
    calls = _wire_urlopen(
        monkeypatch, mod,
        token_handler=lambda n: _token_payload(f"tok{n}", expires_in=30),
    )

    assert mod._get_token() == "tok1"
    assert mod._get_token() == "tok2"
    assert calls["token"] == 2


# ── fail-soft when Keycloak is down ────────────────────────────────────────────

def test_keycloak_down_fails_soft_and_posts_unauthenticated(monkeypatch):
    mod, stub = _load_plugin(monkeypatch, BASE_ENV)
    calls = _wire_urlopen(
        monkeypatch, mod,
        token_handler=lambda n: urllib.error.URLError("keycloak down"),
        post_handler=lambda req, n: _Resp(b"{}"),
    )

    assert mod._get_token() is None
    assert any("token fetch failed" in w for w in stub.warnings)

    # DICOM notification still goes out (bare) — ingestion never blocks on auth
    mod._post_json("http://ai-controller:8000/api/trigger-instance", {"instance_id": "i1"})
    assert len(calls["posts"]) == 1
    assert calls["posts"][0].get_header("Authorization") is None


# ── bearer attachment & 401 retry ──────────────────────────────────────────────

def test_post_sends_bearer_token(monkeypatch):
    mod, stub = _load_plugin(monkeypatch, BASE_ENV)
    calls = _wire_urlopen(
        monkeypatch, mod,
        token_handler=lambda n: _token_payload(f"tok{n}"),
        post_handler=lambda req, n: _Resp(b"{}"),
    )

    mod._post_json("http://ai-controller:8000/api/trigger-instance", {"instance_id": "i1"})

    assert len(calls["posts"]) == 1
    assert calls["posts"][0].get_header("Authorization") == "Bearer tok1"
    body = json.loads(calls["posts"][0].data)
    assert body == {"instance_id": "i1"}


def test_401_refreshes_token_and_retries_once(monkeypatch):
    mod, stub = _load_plugin(monkeypatch, BASE_ENV)

    def post_handler(req, n):
        if n == 1:
            return urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", None, None
            )
        return _Resp(b"{}")

    calls = _wire_urlopen(
        monkeypatch, mod,
        token_handler=lambda n: _token_payload(f"tok{n}"),
        post_handler=post_handler,
    )

    mod._post_json("http://ai-controller:8000/api/trigger-instance", {"instance_id": "i1"})

    assert calls["token"] == 2, "401 must force a token refresh"
    assert len(calls["posts"]) == 2
    assert calls["posts"][1].get_header("Authorization") == "Bearer tok2"


def test_non_401_http_error_propagates(monkeypatch):
    mod, stub = _load_plugin(monkeypatch, BASE_ENV)
    calls = _wire_urlopen(
        monkeypatch, mod,
        token_handler=lambda n: _token_payload(f"tok{n}"),
        post_handler=lambda req, n: urllib.error.HTTPError(
            req.full_url, 503, "Service Unavailable", None, None
        ),
    )

    with pytest.raises(urllib.error.HTTPError):
        mod._post_json("http://ai-controller:8000/api/trigger-instance", {"instance_id": "i1"})

    assert len(calls["posts"]) == 1, "non-401 errors must not be retried"
    assert calls["token"] == 1
