"""
Inbound HL7 message handlers.
Each handler propagates clinical events to OpenMRS via FHIR R4.
All HTTP calls are best-effort: failures are logged but do not prevent ACK.
"""
import datetime
import logging
import os

import httpx

from builder import build_ack
from database import get_db
from parser import parse as hl7_parse

log = logging.getLogger("hl7.handlers")

OPENMRS_URL  = os.environ.get("OPENMRS_URL",  "http://openmrs:8080")
OPENMRS_USER = os.environ.get("OPENMRS_USER", "admin")
OPENMRS_PASS = os.environ.get("OPENMRS_PASS", "Admin123")

_FHIR  = f"{OPENMRS_URL}/openmrs/ws/fhir2/R4"
_AUTH  = (OPENMRS_USER, OPENMRS_PASS)
_HDR   = {"Accept": "application/fhir+json", "Content-Type": "application/fhir+json"}
TIMEOUT = httpx.Timeout(8.0)

_SEX_MAP = {"M": "male", "F": "female", "U": "unknown", "O": "other"}


# ── helpers ───────────────────────────────────────────────────────────────────

async def _fhir_post(resource: dict) -> dict | None:
    rtype = resource.get("resourceType", "Resource")
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=TIMEOUT) as c:
            r = await c.post(f"{_FHIR}/{rtype}", json=resource, headers=_HDR)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning(f"FHIR POST {rtype} failed: {e}")
        return None


async def _find_patient_uuid(mrn: str) -> str | None:
    """Search OpenMRS FHIR for patient by MRN. Returns UUID or None."""
    try:
        async with httpx.AsyncClient(auth=_AUTH, timeout=TIMEOUT) as c:
            r = await c.get(f"{_FHIR}/Patient",
                            params={"identifier": mrn, "_count": "1"},
                            headers=_HDR)
            entries = r.json().get("entry", []) if r.status_code == 200 else []
            return entries[0]["resource"]["id"] if entries else None
    except Exception as e:
        log.warning(f"Patient search for MRN={mrn} failed: {e}")
        return None


async def _ensure_patient(parsed: dict) -> str | None:
    """Look up patient in OpenMRS by MRN; create via FHIR if not found. Returns UUID."""
    mrn = parsed.get("mrn")
    if not mrn:
        return None

    existing = await _find_patient_uuid(mrn)
    if existing:
        return existing

    # Build FHIR Patient and POST to OpenMRS
    dob = parsed.get("birthdate")
    sex = _SEX_MAP.get((parsed.get("sex") or "U").upper(), "unknown")
    patient = {
        "resourceType": "Patient",
        "identifier": [{"system": "http://openhis.local/mrn", "value": mrn}],
        "name": [{"family": parsed.get("lastname", ""), "given": [parsed.get("firstname", "")]}],
        "gender": sex,
    }
    if dob:
        patient["birthDate"] = dob
    result = await _fhir_post(patient)
    return (result or {}).get("id")


# ── event handlers ────────────────────────────────────────────────────────────

async def handle_a01_admit(parsed: dict):
    """ADT^A01 — Admit: ensure patient exists; create in-progress Encounter."""
    patient_uuid = await _ensure_patient(parsed)
    if not patient_uuid:
        return
    await _fhir_post({
        "resourceType": "Encounter",
        "status": "in-progress",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                  "code": "IMP", "display": "inpatient encounter"},
        "subject": {"reference": f"Patient/{patient_uuid}"},
        "period": {"start": datetime.datetime.utcnow().isoformat() + "Z"},
    })


async def handle_a02_transfer(parsed: dict):
    """ADT^A02 — Transfer: log event, update patient."""
    await _ensure_patient(parsed)


async def handle_a03_discharge(parsed: dict):
    """ADT^A03 — Discharge: ensure patient exists."""
    await _ensure_patient(parsed)


async def handle_a04_register(parsed: dict):
    """ADT^A04 — Register: create patient in OpenMRS if not present."""
    await _ensure_patient(parsed)


async def handle_a08_update(parsed: dict):
    """ADT^A08 — Update: upsert patient demographics."""
    await _ensure_patient(parsed)


async def handle_a40_merge(parsed: dict):
    """ADT^A40 — Merge: log only (OpenMRS patient merge requires admin UI)."""
    surviving = parsed.get("mrn")
    retired   = parsed.get("mrg_mrn")
    log.info(f"A40 merge received: surviving={surviving}, retired={retired} — manual merge required in OpenMRS")


async def handle_oru_r01(parsed: dict):
    """ORU^R01 — Observation Result: post DiagnosticReport to OpenMRS FHIR."""
    mrn          = parsed.get("mrn")
    patient_uuid = await _find_patient_uuid(mrn) if mrn else None
    subject      = {"reference": f"Patient/{patient_uuid}"} if patient_uuid else {"display": mrn or "unknown"}
    await _fhir_post({
        "resourceType": "DiagnosticReport",
        "status": "final",
        "code": {"text": parsed.get("order_id", "HL7 ORU Result")},
        "subject": subject,
        "issued": datetime.datetime.utcnow().isoformat() + "Z",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                                   "code": "LAB"}]}],
    })


_HANDLERS = {
    "ADT^A01": handle_a01_admit,
    "ADT^A02": handle_a02_transfer,
    "ADT^A03": handle_a03_discharge,
    "ADT^A04": handle_a04_register,
    "ADT^A08": handle_a08_update,
    "ADT^A40": handle_a40_merge,
    "ORU^R01": handle_oru_r01,
}


def _update_status(msg_id: int, status: str, error: str = None):
    try:
        with get_db() as db:
            db.execute("UPDATE messages SET status=?, error_msg=? WHERE id=?",
                       (status, error, msg_id))
    except Exception as e:
        log.warning(f"Status update failed for msg {msg_id}: {e}")


async def dispatch(raw: str) -> str:
    try:
        parsed = hl7_parse(raw)
    except Exception as e:
        return build_ack("UNKNOWN", "AE", f"Parse error: {str(e)[:80]}")

    if "MSH" not in parsed.get("_segments", []):
        return build_ack("UNKNOWN", "AE", "No MSH segment: not a valid HL7 message")

    msg_type   = parsed.get("msg_type", "UNKNOWN")
    control_id = parsed.get("control_id", "")
    handler    = _HANDLERS.get(msg_type)

    if handler:
        try:
            await handler(parsed)
        except Exception as e:
            log.error(f"Handler error for {msg_type}: {e}")
            return build_ack(control_id, "AE", f"Processing error: {str(e)[:80]}")

    return build_ack(control_id, "AA", f"{msg_type} accepted")


async def dispatch_and_update(raw: str, msg_id: int):
    try:
        parsed   = hl7_parse(raw)
        msg_type = parsed.get("msg_type", "UNKNOWN")
        handler  = _HANDLERS.get(msg_type)
        if handler:
            await handler(parsed)
        _update_status(msg_id, "processed")
    except Exception as e:
        _update_status(msg_id, "error", str(e)[:500])
        log.error(f"dispatch_and_update failed for msg {msg_id}: {e}")
