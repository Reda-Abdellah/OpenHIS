"""
Outbound HL7 message builder + logger.
Called by FHIR bridge (and any internal service) to generate + record
outbound HL7 messages for downstream external systems.
"""
from fastapi import APIRouter, HTTPException
from database import get_db
from builder  import build_adt, build_adt_a40, build_oru_r01
from parser   import parse as hl7_parse

router = APIRouter(prefix="/api/send", tags=["send"])

_VALID_ADT_EVENTS = {'A01', 'A02', 'A03', 'A04', 'A08', 'A11', 'A40'}


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
    event    = (body.get("event") or "A04").lstrip("A").zfill(2)
    full_evt = f"A{event}"
    if full_evt not in _VALID_ADT_EVENTS:
        raise HTTPException(422, f"Invalid ADT event. Use: {sorted(_VALID_ADT_EVENTS)}")

    patient  = body.get("patient", {})
    encounter = body.get("encounter")

    if full_evt == "A40":
        retired = body.get("retired_patient", {})
        raw = build_adt_a40(patient, retired)
    else:
        raw = build_adt(full_evt, patient, encounter,
                        sending_app=body.get("sending_app", "EHR"))

    msg_id = _log_outbound(raw, f"ADT^{full_evt}",
                           patient.get("id"), patient.get("mrn"))
    return {"status": "ok", "msg_type": f"ADT^{full_evt}",
            "msg_id": msg_id, "raw": raw}


@router.post("/oru")
def send_oru(body: dict):
    """Build and log an outbound ORU^R01 message."""
    patient  = body.get("patient", {})
    order_id = body.get("order_id", "")
    results  = body.get("results", [])
    raw      = build_oru_r01(patient, order_id, results,
                              sending_app=body.get("sending_app", "LIS"))
    msg_id   = _log_outbound(raw, "ORU^R01", patient.get("id"), patient.get("mrn"))
    return {"status": "ok", "msg_type": "ORU^R01",
            "msg_id": msg_id, "raw": raw}
