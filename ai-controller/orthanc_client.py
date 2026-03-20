"""Thin async wrapper around Orthanc REST API."""
import os
import httpx

ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://orthanc:8042")
_TIMEOUT = httpx.Timeout(30.0)


async def get_instance_metadata(instance_id: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{ORTHANC_URL}/instances/{instance_id}")
        r.raise_for_status()
        return r.json()


async def get_series_metadata(series_id: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{ORTHANC_URL}/series/{series_id}")
        r.raise_for_status()
        return r.json()


async def get_study_metadata(study_id: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{ORTHANC_URL}/studies/{study_id}")
        r.raise_for_status()
        return r.json()


async def get_instance_file(instance_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as c:
        r = await c.get(f"{ORTHANC_URL}/instances/{instance_id}/file")
        r.raise_for_status()
        return r.content


async def get_instance_tags(instance_id: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{ORTHANC_URL}/instances/{instance_id}/simplified-tags")
        r.raise_for_status()
        return r.json()


async def upload_dicom(dicom_bytes: bytes) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as c:
        r = await c.post(
            f"{ORTHANC_URL}/instances",
            content=dicom_bytes,
            headers={"Content-Type": "application/dicom"},
        )
        r.raise_for_status()
        return r.json()
