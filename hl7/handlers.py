"""
Inbound HL7 message handlers.
Each handler calls EHR / MPI REST APIs to propagate clinical events.
All HTTP calls are best-effort: failures are logged but do not prevent ACK.
"""
import logging, os
import httpx
from parser  import parse as hl7_parse
from builder import build_ack
from database import get_db

log     = logging.getLogger('hl7.handlers')
EHR_URL = os.environ.get('EHR_URL', 'http://ehr:8003/api')
MPI_URL = os.environ.get('MPI_URL', 'http://mpi:8007/api')

TIMEOUT = httpx.Timeout(8.0)


async def _post(url: str, payload: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(url, json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning(f"POST {url} failed: {type(e).__name__}: {e}")
        return None


async def _patch(url: str, payload: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.patch(url, json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning(f"PATCH {url} failed: {type(e).__name__}: {e}")
        return None


async def _get(url: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning(f"GET {url} failed: {type(e).__name__}: {e}")
        return None


def _patient_payload(parsed: dict) -> dict:
    return {
        "mrn":       parsed.get("mrn", ""),
        "firstname": parsed.get("firstname", ""),
        "lastname":  parsed.get("lastname", ""),
        "birthdate": parsed.get("birthdate") or None,
        "sex":       parsed.get("sex") or None,
        "phone":     parsed.get("phone") or None,
    }


async def _ensure_patient(parsed: dict) -> str | None:
    """Look up patient by MRN in EHR; create if not found. Returns EHR patient id."""
    mrn = parsed.get("mrn")
    if not mrn:
        return None
    patients = await _get(f"{EHR_URL}/patients?q={mrn}")
    if patients:
        for p in patients:
            if p.get("mrn") == mrn:
                return p["id"]
    result = await _post(f"{EHR_URL}/patients", _patient_payload(parsed))
    return result.get("id") if result else None


async def handle_a01_admit(parsed: dict):
    """ADT^A01 — Admit patient."""
    patient_id = await _ensure_patient(parsed)
    if patient_id:
        await _post(f"{EHR_URL}/encounters", {
            "patientid":    patient_id,
            "encountertype": "inpatient",
            "ward":         parsed.get("ward") or None,
            "bed":          parsed.get("bed") or None,
        })
    await _post(f"{MPI_URL}/sync/from-ehr", {**_patient_payload(parsed), "id": patient_id})


async def handle_a02_transfer(parsed: dict):
    """ADT^A02 — Transfer patient (update encounter ward/bed)."""
    visit_id = parsed.get("visit_id")
    if visit_id:
        await _patch(f"{EHR_URL}/encounters/{visit_id}", {
            "ward": parsed.get("ward"),
            "bed":  parsed.get("bed"),
        })


async def handle_a03_discharge(parsed: dict):
    """ADT^A03 — Discharge patient."""
    visit_id = parsed.get("visit_id")
    if visit_id:
        await _patch(f"{EHR_URL}/encounters/{visit_id}", {"status": "discharged"})


async def handle_a04_register(parsed: dict):
    """ADT^A04 — Register outpatient."""
    patient_id = await _ensure_patient(parsed)
    if patient_id:
        await _post(f"{MPI_URL}/sync/from-ehr",
                    {**_patient_payload(parsed), "id": patient_id})


async def handle_a08_update(parsed: dict):
    """ADT^A08 — Update patient information."""
    patient_id = await _ensure_patient(parsed)
    if patient_id:
        await _patch(f"{EHR_URL}/patients/{patient_id}", _patient_payload(parsed))
        await _post(f"{MPI_URL}/sync/from-ehr",
                    {**_patient_payload(parsed), "id": patient_id})


async def handle_a40_merge(parsed: dict):
    """ADT^A40 — Merge patients. Uses MRG segment data stored in parsed."""
    surviving_mrn = parsed.get("mrn")
    retired_mrn   = parsed.get("mrg_mrn")     # set by extended parser if MRG present
    if surviving_mrn and retired_mrn:
        await _post(f"{MPI_URL}/patients/merge-by-mrn", {
            "surviving_mrn": surviving_mrn,
            "retired_mrn":   retired_mrn,
        })


async def handle_oru_r01(parsed: dict):
    """ORU^R01 — Observation Result. Forward to EHR CDSS via lab result endpoint."""
    await _post(f"{EHR_URL}/orders/from-lis-result", {
        "ehrpatientid": parsed.get("mrn"),
        "orderid":      parsed.get("order_id"),
        "results":      [],   # raw OBX parsing would be added for production
    })


_HANDLERS = {
    'ADT^A01': handle_a01_admit,
    'ADT^A02': handle_a02_transfer,
    'ADT^A03': handle_a03_discharge,
    'ADT^A04': handle_a04_register,
    'ADT^A08': handle_a08_update,
    'ADT^A40': handle_a40_merge,
    'ORU^R01': handle_oru_r01,
}


def _update_status(msg_id: int, status: str, error: str = None):
    try:
        with get_db() as db:
            db.execute("UPDATE messages SET status=?, error_msg=? WHERE id=?",
                       (status, error, msg_id))
    except Exception as e:
        log.warning(f"Status update failed for msg {msg_id}: {e}")


async def dispatch(raw: str) -> str:
    """
    Parse + route an inbound HL7 message.
    Returns the ACK message string (MLLP path).
    """
    try:
        parsed = hl7_parse(raw)
    except Exception as e:
        return build_ack('UNKNOWN', 'AE', f'Parse error: {str(e)[:80]}')

    msg_type   = parsed.get('msg_type', 'UNKNOWN')
    control_id = parsed.get('control_id', '')
    handler    = _HANDLERS.get(msg_type)

    if handler:
        try:
            await handler(parsed)
        except Exception as e:
            log.error(f"Handler error for {msg_type}: {e}")
            return build_ack(control_id, 'AE', f'Processing error: {str(e)[:80]}')

    return build_ack(control_id, 'AA', f'{msg_type} accepted')


async def dispatch_and_update(raw: str, msg_id: int):
    """Background-task variant that also updates DB status."""
    try:
        parsed  = hl7_parse(raw)
        msg_type = parsed.get('msg_type', 'UNKNOWN')
        handler  = _HANDLERS.get(msg_type)
        if handler:
            await handler(parsed)
        _update_status(msg_id, 'processed')
    except Exception as e:
        _update_status(msg_id, 'error', str(e)[:500])
        log.error(f"dispatch_and_update failed for msg {msg_id}: {e}")
