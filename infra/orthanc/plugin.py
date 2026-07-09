"""
Orthanc Python plugin – forwards every stored instance to:
  1. AI Controller  (existing)  → triggers AI pipeline rules
  2. FHIR Bridge    (new)       → creates FHIR ImagingStudy resource
Both calls are fire-and-forget on daemon threads; either can fail silently.

Both downstream targets now enforce JWT (the AI controller via middleware,
the hub's event-ingest routes via the internal-sync role), so each POST
carries a Keycloak client-credentials bearer token for the orthanc-sa
service account.  Token handling is fail-soft: if Keycloak is unreachable
the request is sent unauthenticated and only a warning is logged — DICOM
ingestion itself is never blocked.

Runs inside Orthanc's embedded Python: stdlib only (urllib, no httpx).
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import os
from typing import Optional

import orthanc

AI_CONTROLLER_URL = os.environ.get("AI_CONTROLLER_URL", "http://ai-controller:8000")
FHIR_BRIDGE_URL   = os.environ.get("FHIR_BRIDGE_URL", "")   # empty = disabled

# Keycloak client-credentials (service account orthanc-sa, internal-sync role).
# An empty token URL disables auth entirely — requests go out bare, matching
# the pre-auth behaviour for deployments without Keycloak.
KC_TOKEN_URL     = os.environ.get("ORTHANC_KC_TOKEN_URL", "")
KC_CLIENT_ID     = os.environ.get("ORTHANC_KC_CLIENT_ID", "orthanc-sa")
KC_CLIENT_SECRET = os.environ.get("ORTHANC_KC_CLIENT_SECRET", "")

_TOKEN_SKEW_S = 60          # refresh this many seconds before real expiry
_token_lock = threading.Lock()
_token_cache: dict = {"token": None, "expires_at": 0.0}


# ── Keycloak service-account token (cached, fail-soft) ─────────────────────────

def _get_token(force_refresh: bool = False) -> Optional[str]:
    """
    Return a cached bearer token for the orthanc-sa service account,
    fetching a fresh one when missing/expired or when *force_refresh* is set.

    Fail-soft by design: any Keycloak error logs ONE clear warning and
    returns None so the notify threads still POST (unauthenticated) and
    DICOM ingestion never breaks when Keycloak is down.
    """
    if not KC_TOKEN_URL:
        return None
    with _token_lock:
        now = time.time()
        if (
            not force_refresh
            and _token_cache["token"]
            and now < _token_cache["expires_at"] - _TOKEN_SKEW_S
        ):
            return _token_cache["token"]
        try:
            body = urllib.parse.urlencode({
                "grant_type":    "client_credentials",
                "client_id":     KC_CLIENT_ID,
                "client_secret": KC_CLIENT_SECRET,
            }).encode()
            req = urllib.request.Request(
                KC_TOKEN_URL,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            _token_cache["token"] = data["access_token"]
            _token_cache["expires_at"] = now + float(data.get("expires_in", _TOKEN_SKEW_S))
            return _token_cache["token"]
        except Exception as e:  # fail soft — never block DICOM ingestion on auth
            _token_cache["token"] = None
            _token_cache["expires_at"] = 0.0
            orthanc.LogWarning(
                f"[plugin] Keycloak token fetch failed ({KC_TOKEN_URL}, "
                f"client {KC_CLIENT_ID}) — continuing without auth: {e}"
            )
            return None


# ── shared helper ──────────────────────────────────────────────────────────────

def _do_post(url: str, payload: bytes, token: Optional[str], timeout: int) -> None:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout):
        pass


def _post_json(url: str, data: dict, timeout: int = 8) -> None:
    """
    POST a JSON payload with a service-account bearer token (when Keycloak
    is configured).  On 401 the cached token is discarded and the request
    retried once with a fresh one.  Raises on HTTP/network error.
    """
    payload = json.dumps(data).encode()
    try:
        _do_post(url, payload, _get_token(), timeout)
    except urllib.error.HTTPError as e:
        if e.code != 401 or not KC_TOKEN_URL:
            raise
        orthanc.LogWarning(f"[plugin] 401 from {url} — refreshing token and retrying once")
        _do_post(url, payload, _get_token(force_refresh=True), timeout)


# ── per-target notifiers ───────────────────────────────────────────────────────

def _notify_ai_controller(instance_id: str) -> None:
    """Notify the AI Controller. Runs in a daemon thread."""
    try:
        _post_json(
            f"{AI_CONTROLLER_URL}/api/trigger-instance",
            {"instance_id": instance_id},
        )
        orthanc.LogInfo(f"[plugin] AI Controller notified: {instance_id}")
    except urllib.error.URLError as e:
        orthanc.LogWarning(f"[plugin] AI Controller unreachable for {instance_id}: {e}")
    except Exception as e:
        orthanc.LogError(f"[plugin] AI trigger failed for {instance_id}: {e}")


def _notify_fhir_bridge(instance_id: str) -> None:
    """
    Notify the FHIR Bridge with the stored instance ID.
    The bridge will:
      - Resolve instance → series → study via Orthanc REST
      - Build a FHIR R4 ImagingStudy resource
      - PUT it to the HAPI FHIR server (if FHIR_ENABLED=true)
    Runs in a daemon thread; skipped entirely if FHIR_BRIDGE_URL is unset.
    """
    if not FHIR_BRIDGE_URL:
        return
    try:
        _post_json(
            f"{FHIR_BRIDGE_URL}/api/events/dicom-stored",
            {"instanceId": instance_id},
            timeout=6,            # slightly shorter: FHIR bridge is optional
        )
        orthanc.LogInfo(f"[plugin] FHIR Bridge notified: {instance_id}")
    except urllib.error.URLError as e:
        # Degraded gracefully — FHIR bridge may not be running in all environments
        orthanc.LogWarning(f"[plugin] FHIR Bridge unreachable for {instance_id}: {e}")
    except Exception as e:
        orthanc.LogError(f"[plugin] FHIR Bridge notify failed for {instance_id}: {e}")


# ── Orthanc callback ───────────────────────────────────────────────────────────

def on_stored_instance(dicom, instance_id: str) -> None:
    """
    Called by Orthanc for every newly stored DICOM instance.
    Spawns two independent daemon threads — one per downstream target.
    Each thread has its own try/except so failures are fully isolated.
    """
    threading.Thread(
        target=_notify_ai_controller,
        args=(instance_id,),
        daemon=True,
        name=f"ai-{instance_id[:8]}",
    ).start()

    threading.Thread(
        target=_notify_fhir_bridge,
        args=(instance_id,),
        daemon=True,
        name=f"fhir-{instance_id[:8]}",
    ).start()


# ── registration ───────────────────────────────────────────────────────────────

orthanc.RegisterOnStoredInstanceCallback(on_stored_instance)
orthanc.LogInfo(f"[plugin] AI Controller target  : {AI_CONTROLLER_URL}")
orthanc.LogInfo(f"[plugin] FHIR Bridge target    : {FHIR_BRIDGE_URL or '(disabled)'}")
orthanc.LogInfo(
    "[plugin] Keycloak auth         : "
    + (f"{KC_TOKEN_URL} (client {KC_CLIENT_ID})" if KC_TOKEN_URL else "(disabled — unauthenticated)")
)
