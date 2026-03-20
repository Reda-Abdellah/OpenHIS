"""
Orthanc Python plugin – forwards every stored instance to the AI Controller.
The controller deduplicates at series level and evaluates routing rules.
"""
import json
import threading
import urllib.request
import urllib.error
import os
import orthanc

AI_CONTROLLER_URL = os.environ.get("AI_CONTROLLER_URL", "http://ai-controller:8000")


def _post_json(url: str, data: dict, timeout: int = 8):
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=timeout)


def on_stored_instance(dicom, instance_id: str):
    """
    Called by Orthanc for every newly stored DICOM instance.
    Sends a lightweight trigger to the AI Controller asynchronously.
    """
    def trigger():
        try:
            _post_json(
                f"{AI_CONTROLLER_URL}/api/trigger-instance",
                {"instance_id": instance_id},
            )
            orthanc.LogInfo(f"AI Controller notified: {instance_id}")
        except urllib.error.URLError as e:
            orthanc.LogWarning(f"AI Controller unreachable for {instance_id}: {e}")
        except Exception as e:
            orthanc.LogError(f"AI trigger failed for {instance_id}: {e}")

    threading.Thread(target=trigger, daemon=True).start()


orthanc.RegisterOnStoredInstanceCallback(on_stored_instance)
orthanc.LogInfo(f"AI Controller plugin loaded – target {AI_CONTROLLER_URL}")
