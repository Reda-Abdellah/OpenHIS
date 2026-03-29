"""
bus_consumer — HL7 subscriber for the openhis:events Redis stream.

Listens for lab_result.ready events and builds an outbound ORU^R01
message for every completed DiagnosticReport, logging it to the
messages table so it can be forwarded to downstream external systems.

Consumer group: hl7
Consumer name:  hl7-1
"""
import asyncio
import json
import logging
import os

import httpx
import redis.asyncio as aioredis

from builder import build_oru_r01
from database import get_db

log = logging.getLogger("hl7.bus")

STREAM   = "openhis:events"
GROUP    = "hl7"
CONSUMER = "hl7-1"
BLOCK_MS = 5_000
BATCH    = 20

REDIS_URL     = os.environ.get("REDIS_URL", "")
OPENELIS_URL  = os.environ.get("OPENELIS_URL", "http://openelis:8080")
OPENELIS_USER = os.environ.get("OPENELIS_USER")
OPENELIS_PASS = os.environ.get("OPENELIS_PASS")

_client: aioredis.Redis | None = None


def _get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _client


async def _fetch_diagnostic_report(oe_id: str) -> dict | None:
    """Fetch a DiagnosticReport from OpenELIS FHIR by its ID."""
    url = f"{OPENELIS_URL}/fhir/R4/DiagnosticReport/{oe_id}"
    try:
        async with httpx.AsyncClient(
            auth=(OPENELIS_USER, OPENELIS_PASS), timeout=8.0
        ) as c:
            r = await c.get(url, headers={"Accept": "application/fhir+json"})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        log.warning("Failed to fetch DiagnosticReport %s: %s", oe_id, e)
    return None


def _extract_oru_fields(report: dict) -> tuple[dict, str, list]:
    """
    Extract patient, order_id, and result observations from a FHIR DiagnosticReport.
    Returns (patient_dict, order_id, results_list).
    """
    subject_ref = (report.get("subject") or {}).get("reference", "")
    mrn = subject_ref.split("/")[-1] if "/" in subject_ref else subject_ref

    patient = {"mrn": mrn}

    order_id = ""
    for ident in report.get("identifier", []):
        if ident.get("system", "").endswith("order-id"):
            order_id = ident.get("value", "")
            break
    if not order_id:
        order_id = report.get("id", "")

    results = []
    for obs in report.get("contained", []):
        if obs.get("resourceType") != "Observation":
            continue
        code_text = (obs.get("code") or {}).get("text", "")
        vq = obs.get("valueQuantity") or {}
        results.append({
            "analyte":      code_text,
            "value":        str(vq.get("value", "")),
            "unit":         vq.get("unit", ""),
            "flag":         obs.get("interpretation", [{}])[0].get("coding", [{}])[0].get("code", "N") if obs.get("interpretation") else "N",
            "referencerange": "",
        })

    return patient, order_id, results


def _log_outbound(raw: str, msg_type: str, patient_id: str = "") -> None:
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO messages"
                "(direction,msg_type,control_id,sending_app,patient_id,raw,status) "
                "VALUES('outbound',?,NULL,'LIS',?,?,'sent')",
                (msg_type, patient_id, raw),
            )
    except Exception as e:
        log.warning("Failed to log outbound message: %s", e)


async def _handle_lab_result_ready(payload: dict) -> None:
    """
    Build and log an outbound ORU^R01 for a completed lab result.
    payload: {"oe_id": str, "subject": str}
    """
    oe_id = payload.get("oe_id")
    if not oe_id:
        return

    report = await _fetch_diagnostic_report(oe_id)
    if not report:
        log.warning("Could not fetch DiagnosticReport %s — skipping ORU^R01", oe_id)
        return

    patient, order_id, results = _extract_oru_fields(report)
    if not results:
        log.debug("DiagnosticReport %s has no contained observations — skipping", oe_id)
        return

    raw = build_oru_r01(patient, order_id, results, sending_app="LIS")
    _log_outbound(raw, "ORU^R01", patient.get("mrn", ""))
    log.info("Outbound ORU^R01 built for DiagnosticReport %s (order=%s, obs=%d)",
             oe_id, order_id, len(results))


_HANDLERS = {
    "lab_result.ready": _handle_lab_result_ready,
}


async def _process_message(entry_id: str, fields: dict) -> None:
    event_type = fields.get("type", "")
    handler = _HANDLERS.get(event_type)
    if handler is None:
        return
    try:
        payload = json.loads(fields.get("payload", "{}"))
        await handler(payload)
    except Exception as exc:
        log.error("Error handling %s (%s): %s", event_type, entry_id, exc)


async def consume_loop() -> None:
    """Main consumer loop — runs until the task is cancelled."""
    if not REDIS_URL:
        log.info("REDIS_URL not set — HL7 bus consumer disabled")
        return

    r = _get_client()

    try:
        await r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            log.warning("xgroup_create: %s", e)

    log.info("HL7 bus consumer started (stream=%s group=%s)", STREAM, GROUP)

    while True:
        try:
            results = await r.xreadgroup(
                groupname=GROUP,
                consumername=CONSUMER,
                streams={STREAM: ">"},
                count=BATCH,
                block=BLOCK_MS,
            )
            if not results:
                continue

            for _stream, messages in results:
                for entry_id, fields in messages:
                    await _process_message(entry_id, fields)
                    await r.xack(STREAM, GROUP, entry_id)

        except asyncio.CancelledError:
            log.info("HL7 bus consumer stopping")
            break
        except Exception as exc:
            log.error("Bus consumer error: %s — retrying in 5 s", exc)
            await asyncio.sleep(5)
