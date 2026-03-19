import orthanc
import json
import http.client
import os
import uuid
import urllib.parse

AI_SERVICE_URL = os.environ.get("AI_SERVICE_URL", "http://ai-service:8000")
_parsed      = urllib.parse.urlparse(AI_SERVICE_URL)
_AI_HOST     = _parsed.hostname or "ai-service"
_AI_PORT     = _parsed.port    or 80


def _build_multipart(dicom_bytes: bytes, boundary: str) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="image.dcm"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return header + bytes(dicom_bytes) + footer


def _post_to_ai(dicom_bytes: bytes) -> dict:
    boundary = "OrthancAIBoundary" + uuid.uuid4().hex
    body     = _build_multipart(dicom_bytes, boundary)
    headers  = {
        "Content-Type"  : f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    conn = http.client.HTTPConnection(_AI_HOST, _AI_PORT, timeout=120)
    try:
        conn.request("POST", "/predict", body=body, headers=headers)
        resp = conn.getresponse()
        raw  = resp.read()
        if resp.status != 200:
            raise RuntimeError(
                f"AI service HTTP {resp.status}: {raw[:300].decode('utf-8', errors='replace')}"
            )
        return json.loads(raw)
    finally:
        conn.close()


def OnStoredInstance(dicom, instanceId):
    try:
        tags_raw   = orthanc.RestApiGet(f"/instances/{instanceId}/simplified-tags")
        tags       = json.loads(tags_raw)
        modality   = tags.get("Modality", "")

        if modality not in ("CR", "DX"):
            return

        orthanc.LogInfo(
            f"[AI-Plugin] Modality={modality} | instance={instanceId} — sending to AI service"
        )

        dicom_bytes = orthanc.RestApiGet(f"/instances/{instanceId}/file")
        result      = _post_to_ai(dicom_bytes)

        top3 = result.get("top3", [])
        orthanc.LogInfo(
            f"[AI-Plugin] Top-3 for {instanceId}: "
            + ", ".join(f"{e['pathology']}={e['probability']}" for e in top3)
        )

        orthanc.RestApiPut(
            f"/instances/{instanceId}/metadata/9999",
            json.dumps(result),
        )
        orthanc.LogInfo(f"[AI-Plugin] Result stored in metadata/9999 for {instanceId}")

    except Exception as exc:
        orthanc.LogWarning(f"[AI-Plugin] Non-fatal error for instance {instanceId}: {exc}")


orthanc.RegisterOnStoredInstanceCallback(OnStoredInstance)
