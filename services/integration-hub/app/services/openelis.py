"""
OpenELIS client — FHIR R4 access.

OpenELIS Global 2 exposes its HAPI FHIR endpoint at
/OpenELIS-Global/fhir/ (no `/R4/` segment). It does NOT validate Keycloak
JWTs — the FHIR resource chain accepts HTTP Basic auth with an OpenELIS
local admin user. We use that directly; JWT introspection at the OE layer
is an upstream roadmap item and would belong in a realm-aware resource
server, not here.
"""
import logging
from typing import Optional
import httpx
from app.config import OPENELIS_URL, OPENELIS_USER, OPENELIS_PASSWORD

log = logging.getLogger("hub.openelis")

_FHIR = f"{OPENELIS_URL}/OpenELIS-Global/fhir"
_HDR  = {"Accept": "application/fhir+json", "Content-Type": "application/fhir+json"}
_AUTH = httpx.BasicAuth(OPENELIS_USER, OPENELIS_PASSWORD)


async def _auth_headers() -> dict:
    return dict(_HDR)


async def health_check() -> bool:
    try:
        hdrs = await _auth_headers()
        async with httpx.AsyncClient(timeout=8, auth=_AUTH) as c:
            r = await c.get(f"{_FHIR}/metadata", headers=hdrs)
            return r.status_code == 200
    except Exception:
        return False


async def upsert_patient(patient: dict) -> Optional[str]:
    """
    Create or update a FHIR Patient in OpenELIS.
    Uses the first identifier value to search for an existing record.
    Returns the OpenELIS patient id, or None on failure.
    """
    identifiers = patient.get("identifier", [])
    if not identifiers:
        log.warning(f"Patient {patient.get('id')} has no identifiers — skipping")
        return None

    system = identifiers[0].get("system", "")
    value  = identifiers[0].get("value", "")
    search_param = f"{system}|{value}" if system else value

    try:
        hdrs = await _auth_headers()
        async with httpx.AsyncClient(timeout=15, auth=_AUTH) as c:
            r = await c.get(f"{_FHIR}/Patient",
                            params={"identifier": search_param},
                            headers=hdrs)
            r.raise_for_status()
            entries = r.json().get("entry", [])

            if entries:
                oe_id = entries[0]["resource"]["id"]
                patched = dict(patient)
                patched["id"] = oe_id
                r2 = await c.put(f"{_FHIR}/Patient/{oe_id}", json=patched, headers=hdrs)
                r2.raise_for_status()
                log.debug(f"OpenELIS Patient/{oe_id} updated")
                return oe_id
            else:
                r2 = await c.post(f"{_FHIR}/Patient", json=patient, headers=hdrs)
                r2.raise_for_status()
                oe_id = r2.json().get("id")
                log.info(f"OpenELIS Patient/{oe_id} created (identifier={value})")
                return oe_id
    except Exception as e:
        log.warning(f"upsert_patient({value}): {e}")
        return None


async def create_service_request(sr: dict) -> Optional[str]:
    """
    Submit a lab order (ServiceRequest) to OpenELIS.
    Returns the OpenELIS resource id, or None on failure.
    """
    identifiers = sr.get("identifier", [])
    if identifiers:
        value = identifiers[0].get("value", "")
        try:
            hdrs = await _auth_headers()
            async with httpx.AsyncClient(timeout=10, auth=_AUTH) as c:
                r = await c.get(f"{_FHIR}/ServiceRequest",
                                params={"identifier": value},
                                headers=hdrs)
                entries = r.json().get("entry", []) if r.status_code == 200 else []
                if entries:
                    oe_id = entries[0]["resource"]["id"]
                    log.debug(f"ServiceRequest/{oe_id} already in OpenELIS — skipping")
                    return oe_id
        except Exception:
            pass

    try:
        hdrs = await _auth_headers()
        async with httpx.AsyncClient(timeout=15, auth=_AUTH) as c:
            r = await c.post(f"{_FHIR}/ServiceRequest", json=sr, headers=hdrs)
            r.raise_for_status()
            oe_id = r.json().get("id")
            log.info(f"OpenELIS ServiceRequest/{oe_id} created")
            return oe_id
    except Exception as e:
        log.warning(f"create_service_request: {e}")
        return None


async def get_completed_reports(count: int = 50) -> list[dict]:
    """Fetch final DiagnosticReports from OpenELIS for back-routing to OpenMRS."""
    try:
        hdrs = await _auth_headers()
        async with httpx.AsyncClient(timeout=15, auth=_AUTH) as c:
            r = await c.get(f"{_FHIR}/DiagnosticReport",
                            params={"status": "final", "_count": count},
                            headers=hdrs)
            r.raise_for_status()
            return [e["resource"] for e in r.json().get("entry", [])]
    except Exception as e:
        log.warning(f"get_completed_reports: {e}")
        return []
