"""
Outbound HL7 message builder + logger.
Called by FHIR bridge (and any internal service) to generate + record
outbound HL7 messages for downstream external systems.
"""
import logging
import os

from fastapi import APIRouter, HTTPException
from database import get_db
from builder  import build_adt, build_adt_a40, build_oru_r01, build_orm_o01
from mllp_client import send_mllp
from parser   import parse as hl7_parse

log = logging.getLogger("hl7.send")
router = APIRouter(prefix="/api/send", tags=["send"])

# Where to deliver ORM^O01 messages over MLLP. Unset disables MLLP
# delivery (the message is still built + logged in the local DB for
# audit) — set it to a downstream LIS/router listener to transmit.
ORM_MLLP_HOST = os.environ.get("ORM_MLLP_HOST", "")
ORM_MLLP_PORT = int(os.environ.get("ORM_MLLP_PORT", "6661"))

_VALID_ADT_EVENTS = {'A01', 'A02', 'A03', 'A04', 'A08', 'A11', 'A40'}

# Flat body keys accepted as aliases for the nested patient dict
# (portal / e2e callers post {"mrn": ..., "first_name": ...} directly).
_FLAT_PATIENT_KEYS = {
    "mrn":       ("mrn",),
    "firstname": ("first_name", "firstname"),
    "lastname":  ("last_name", "lastname"),
    "birthdate": ("birth_date", "birthdate"),
    "sex":       ("gender", "sex"),
}


def _patient_from(body: dict) -> dict:
    """Normalise a request body into a patient dict for the PID builder.

    Accepts both the nested form ({"patient": {...}}) and the flat form
    ({"mrn", "first_name", ...}); nested values win over flat aliases.
    """
    patient = dict(body.get("patient") or {})
    for key, aliases in _FLAT_PATIENT_KEYS.items():
        for alias in aliases:
            value = body.get(alias)
            if value is not None:
                patient.setdefault(key, value)
                break
    return patient


def _log_outbound(raw: str, msg_type: str, patient_id: str = None,
                  patient_name: str = None) -> int:
    parsed = hl7_parse(raw)
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO messages"
            "(direction,msg_type,control_id,sending_app,patient_id,patient_name,raw,status) "
            "VALUES('outbound',?,?,?,?,?,?,'sent')",
            (msg_type,
             parsed.get("control_id"),
             parsed.get("sending_app", "HL7-SVC"),
             patient_id or parsed.get("mrn"),
             patient_name or parsed.get("patient_name"),
             raw)
        )
        return cur.lastrowid


@router.post("/adt")
def send_adt(body: dict):
    """
    Build and log an outbound ADT message.
    body.event : 'A01'|'A03'|'A04'|'A08'|'A40'
    body.patient : patient dict
    body.encounter : encounter dict (optional)
    """
    event    = (body.get("event") or body.get("event_code") or "A04").lstrip("A").zfill(2)
    full_evt = f"A{event}"
    if full_evt not in _VALID_ADT_EVENTS:
        raise HTTPException(422, f"Invalid ADT event. Use: {sorted(_VALID_ADT_EVENTS)}")

    patient  = _patient_from(body)
    encounter = body.get("encounter")

    if full_evt == "A40":
        retired = body.get("retired_patient", {})
        raw = build_adt_a40(patient, retired)
    else:
        raw = build_adt(full_evt, patient, encounter,
                        sending_app=body.get("sending_app", "EHR"))

    msg_id = _log_outbound(raw, f"ADT^{full_evt}")
    return {"status": "ok", "msg_type": f"ADT^{full_evt}",
            "msg_id": msg_id, "raw": raw}


@router.post("/oru")
def send_oru(body: dict):
    """Build and log an outbound ORU^R01 message."""
    patient  = _patient_from(body)
    order_id = body.get("order_id", "")
    results  = body.get("results", [])
    if not results and (body.get("test_code") or body.get("test_name")):
        # Flat single-result form used by the portal / e2e suite.
        results = [{
            "analyte":        body.get("test_code") or body.get("test_name", ""),
            "value":          body.get("value", ""),
            "unit":           body.get("unit", ""),
            "flag":           body.get("abnormal_flag", "N"),
            "referencerange": body.get("reference_range", ""),
        }]
    raw      = build_oru_r01(patient, order_id, results,
                              sending_app=body.get("sending_app", "LIS"))
    msg_id   = _log_outbound(raw, "ORU^R01")
    return {"status": "ok", "msg_type": "ORU^R01",
            "msg_id": msg_id, "raw": raw}


@router.post("/orm")
async def send_orm(body: dict):
    """Build an ORM^O01 (Order Message) and transmit it via MLLP.

    Body:
      patient:    {id, mrn, firstname, lastname, birthdate, sex}
      order_id:   placer order id (caller-generated)
      tests:      [{loinc, name}]  — list of tests on the order
      notes:      [str]            — free-text annotations to attach
      sending_app: optional, default 'OPENHIS'
      priority:   optional HL7 priority code (R/S/A/P), default R
      transmit:   bool, default True. If False, build + log only
                  (no MLLP). Useful for unit-level testing.

    Returns the raw message, the audit row id, and the MLLP ACK
    string (or transmit=False).
    """
    patient   = _patient_from(body)
    order_id  = body.get("order_id") or ""
    tests     = body.get("tests") or []
    notes     = body.get("notes") or []
    transmit  = body.get("transmit", True)

    if not order_id:
        raise HTTPException(422, "order_id is required")
    if not tests:
        raise HTTPException(422, "tests must be a non-empty list")

    raw = build_orm_o01(
        patient, order_id, tests,
        notes=notes,
        sending_app=body.get("sending_app", "OPENHIS"),
        priority=body.get("priority", "R"),
    )
    msg_id = _log_outbound(raw, "ORM^O01")

    ack: str | None = None
    if transmit and not ORM_MLLP_HOST:
        return {"status": "logged_only", "msg_type": "ORM^O01",
                "msg_id": msg_id, "raw": raw,
                "mllp_error": "ORM_MLLP_HOST not configured"}
    if transmit:
        try:
            ack = await send_mllp(ORM_MLLP_HOST, ORM_MLLP_PORT, raw)
        except Exception as exc:
            log.warning("ORM MLLP send to %s:%s failed: %s",
                          ORM_MLLP_HOST, ORM_MLLP_PORT, exc)
            return {
                "status": "logged_only", "msg_type": "ORM^O01",
                "msg_id": msg_id, "raw": raw,
                "mllp_error": str(exc)[:200],
            }
    return {
        "status": "sent" if transmit else "logged",
        "msg_type": "ORM^O01", "msg_id": msg_id,
        "raw": raw, "ack": ack,
    }
