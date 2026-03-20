"""
Orthanc Python plugin
Triggers AI analysis for every stored DICOM instance.
"""

import json
import threading
import urllib.request
import urllib.error
import os

import orthanc

AI_SERVICE_URL = os.environ.get("AI_SERVICE_URL", "http://ai-service:8000")


def _post_json(url: str, data: dict, timeout: int = 8):
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data    = payload,
        headers = {"Content-Type": "application/json"},
        method  = "POST",
    )
    urllib.request.urlopen(req, timeout=timeout)


def on_stored_instance(dicom, instance_id):
    """
    Called by Orthanc for every newly stored DICOM instance.
    Fires a background POST to the AI service so Orthanc is
    never blocked waiting for the analysis result.
    """
    def _trigger():
        try:
            _post_json(f"{AI_SERVICE_URL}/analyze",
                       {"instance_id": instance_id})
            orthanc.LogInfo(f"AI analysis queued → {instance_id}")
        except urllib.error.URLError as e:
            orthanc.LogWarning(f"AI service unreachable for {instance_id}: {e}")
        except Exception as e:
            orthanc.LogError(f"AI trigger failed for {instance_id}: {e}")

    threading.Thread(target=_trigger, daemon=True).start()


orthanc.RegisterOnStoredInstanceCallback(on_stored_instance)
orthanc.LogInfo("AI analysis plugin loaded — target: " + AI_SERVICE_URL)
