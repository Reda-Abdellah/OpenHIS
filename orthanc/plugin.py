"""
Orthanc Python plugin – forwards every stored instance to:
  1. AI Controller  (existing)  → triggers AI pipeline rules
  2. FHIR Bridge    (new)       → creates FHIR ImagingStudy resource
Both calls are fire-and-forget on daemon threads; either can fail silently.
"""
import json
import threading
import urllib.request
import urllib.error
import os
import orthanc

AI_CONTROLLER_URL = os.environ.get("AI_CONTROLLER_URL", "http://ai-controller:8000")
FHIR_BRIDGE_URL   = os.environ.get("FHIR_BRIDGE_URL", "")   # empty = disabled


# ── shared helper ──────────────────────────────────────────────────────────────

def _post_json(url: str, data: dict, timeout: int = 8) -> None:
    """POST a JSON payload. Raises on HTTP/network error."""
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=timeout)


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
