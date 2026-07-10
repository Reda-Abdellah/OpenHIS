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
import os
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
    """Probe the public FHIR /metadata endpoint with no credentials (DEF-001).

    The FHIR capability statement is public; sending Basic auth here would
    let a bad OPENELIS_PASSWORD masquerade as an upstream outage.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{_FHIR}/metadata", headers=_HDR)
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
                # OE's façade returns 201 with an EMPTY body — the created
                # resource id only rides in the Location header (when at
                # all). Treating that as a failure made the bus consumer
                # retry and create duplicates.
                oe_id = None
                if r2.content:
                    try:
                        oe_id = r2.json().get("id")
                    except ValueError:
                        pass
                if not oe_id:
                    loc = r2.headers.get("Location") or r2.headers.get("Content-Location") or ""
                    parts = [p for p in loc.split("/") if p]
                    if "Patient" in parts and len(parts) > parts.index("Patient") + 1:
                        oe_id = parts[parts.index("Patient") + 1]
                oe_id = oe_id or "created"
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


# OpenELIS's own FHIR servlet handles only [Observation, Organization,
# Patient, Practitioner] — DiagnosticReports produced by the LIS surface
# ONLY on its backing FHIR store (write-through by RegisterFhirHooksTask;
# see compose/profiles/laboratory.yml::oe-fhir-store). Reads of lab
# results therefore target the store directly; it is unauthenticated but
# reachable only inside the compose network (nginx route is
# subnet-restricted).
_STORE = os.environ.get("OPENELIS_FHIR_STORE_URL", "http://oe-fhir-store:8080/fhir")


async def get_diagnostic_report(oe_id: str) -> Optional[dict]:
    """Fetch a DiagnosticReport by id from OpenELIS's FHIR store, or None.

    Fail-soft read used by the hub's /api/context surface (audited,
    hub-mediated reads for native services — no direct OpenELIS access).
    """
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{_STORE}/DiagnosticReport/{oe_id}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning(f"get DiagnosticReport/{oe_id}: {e}")
        return None


async def get_completed_reports(count: int = 50) -> list[dict]:
    """Fetch final DiagnosticReports from OpenELIS's FHIR store."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{_STORE}/DiagnosticReport",
                            params={"status": "final", "_count": count})
            r.raise_for_status()
            return [e["resource"] for e in r.json().get("entry", [])]
    except Exception as e:
        log.warning(f"get_completed_reports: {e}")
        return []
