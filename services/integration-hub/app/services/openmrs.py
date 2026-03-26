"""
OpenMRS client — FHIR R4 access.

Fetches patients and ServiceRequests from OpenMRS FHIR R4, and writes
DiagnosticReports back to OpenMRS.
"""
import logging
from typing import Optional
import httpx
from app.config import OPENMRS_URL, OPENMRS_USER, OPENMRS_PASS

log = logging.getLogger("hub.openmrs")

_FHIR = f"{OPENMRS_URL}/openmrs/ws/fhir2/R4"
_AUTH = (OPENMRS_USER, OPENMRS_PASS)
_HDR  = {"Accept": "application/fhir+json", "Content-Type": "application/fhir+json"}


async def health_check() -> bool:
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=8) as c:
            r = await c.get(f"{_FHIR}/metadata")
            return r.status_code == 200
    except Exception:
        return False


async def get_recent_patients(count: int = 100) -> list[dict]:
    """Return the most recently updated patients, sorted newest first."""
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=20) as c:
            r = await c.get(f"{_FHIR}/Patient",
                            params={"_count": count, "_sort": "-_lastUpdated"},
                            headers=_HDR)
            r.raise_for_status()
            return [e["resource"] for e in r.json().get("entry", [])]
    except Exception as e:
        log.warning(f"get_recent_patients: {e}")
        return []


async def get_active_service_requests(count: int = 100) -> list[dict]:
    """Return active lab/radiology orders from OpenMRS."""
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=20) as c:
            r = await c.get(f"{_FHIR}/ServiceRequest",
                            params={"status": "active", "_count": count},
                            headers=_HDR)
            r.raise_for_status()
            return [e["resource"] for e in r.json().get("entry", [])]
    except Exception as e:
        log.warning(f"get_active_service_requests: {e}")
        return []


async def post_diagnostic_report(report: dict) -> bool:
    """Write a completed DiagnosticReport back to OpenMRS FHIR."""
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=15) as c:
            r = await c.post(f"{_FHIR}/DiagnosticReport", json=report, headers=_HDR)
            r.raise_for_status()
            log.info(f"DiagnosticReport → OpenMRS HTTP {r.status_code}")
            return True
    except Exception as e:
        log.warning(f"post_diagnostic_report: {e}")
        return False


async def find_patient_uuid(identifier_value: str) -> Optional[str]:
    """Return the OpenMRS patient UUID matching a given identifier value."""
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=10) as c:
            r = await c.get(f"{_FHIR}/Patient",
                            params={"identifier": identifier_value},
                            headers=_HDR)
            r.raise_for_status()
            entries = r.json().get("entry", [])
            return entries[0]["resource"]["id"] if entries else None
    except Exception as e:
        log.warning(f"find_patient_uuid({identifier_value}): {e}")
        return None
