"""
bus_consumer — HL7 subscriber for the openhis:events Redis stream.

Listens for:
  * ``lab_result.ready``           → outbound ORU^R01 for every completed
                                     DiagnosticReport (forwarding/audit).

Consumption goes through the SDK BusConsumer: entries are acked only
after successful handling; poison entries land on openhis:events:dlq
after max_delivery attempts (see docs/adr/0005-bus-dead-letter-semantics.md).

Consumer group: hl7
Consumer name:  hl7-1
"""
import logging
import os
import time

import httpx

from builder import build_oru_r01
from database import get_db
from openhis_sdk.bus import BusConsumer
from parser import parse as hl7_parse

log = logging.getLogger("hl7.bus")

GROUP    = "hl7"
CONSUMER = "hl7-1"

REDIS_URL           = os.environ.get("REDIS_URL", "")
INTEGRATION_HUB_URL = os.environ.get("INTEGRATION_HUB_URL",
                                     "http://integration-hub:8012")

# Keycloak service-account token cache for the hub calls below.
#
# Deliberately self-contained rather than importing services/hl7/token.py:
# a module literally named ``token`` collides with the stdlib ``token``
# module (pulled in by ``tokenize``), so importing it is only safe where
# sys.path ordering is guaranteed — the consumer keeps its own small,
# fail-soft cache instead.
_token_cache: dict = {"token": None, "expires_at": 0.0}


async def _service_token(force_refresh: bool = False) -> str | None:
    """client_credentials token from the hl7-sa Keycloak SA, cached.

    Reads KEYCLOAK_TOKEN_URL / KEYCLOAK_CLIENT_ID / KEYCLOAK_CLIENT_SECRET
    (already in the hl7 container env — see compose/base.yml). Returns
    ``None`` on any failure: the hub then rejects the unauthenticated
    request with 401 and the caller degrades gracefully.
    """
    token_url     = os.environ.get("KEYCLOAK_TOKEN_URL")
    client_id     = os.environ.get("KEYCLOAK_CLIENT_ID")
    client_secret = os.environ.get("KEYCLOAK_CLIENT_SECRET")
    if not (token_url and client_id and client_secret):
        log.warning("KEYCLOAK_TOKEN_URL/CLIENT_ID/CLIENT_SECRET not set — "
                    "hub calls will go out unauthenticated and fail closed")
        return None
    if not force_refresh and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(token_url, data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
            })
            r.raise_for_status()
            data = r.json()
            _token_cache["token"] = data["access_token"]
            _token_cache["expires_at"] = (
                time.time() + float(data.get("expires_in", 60))
            )
            return _token_cache["token"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("service token fetch failed: %s", exc)
        return None


async def _fetch_diagnostic_report(oe_id: str) -> dict | None:
    """Resolve a DiagnosticReport through the integration-hub.

    GET {INTEGRATION_HUB_URL}/api/context/diagnostic-report/{oe_id}
    (internal-sync gated) with the hl7-sa bearer token. This replaces the
    former direct OpenELIS FHIR read, whose credentials were never
    plumbed into the hl7 container (OPENELIS_USER/OPENELIS_PASS unset in
    every compose file) — and which violated the adapter rule anyway.
    Going through the hub means the read is audited and uses the hub's
    single OpenELIS adapter.

    Fail-soft: returns ``None`` on any failure (hub down, 401/404, token
    fetch failure) — the caller skips the ORU^R01 rather than crash.
    A stale/rejected token is refreshed once on 401.
    """
    if not oe_id:
        return None
    url = f"{INTEGRATION_HUB_URL}/api/context/diagnostic-report/{oe_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            headers = {"Accept": "application/json"}
            token = await _service_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = await c.get(url, headers=headers)
            if r.status_code == 401:
                token = await _service_token(force_refresh=True)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                    r = await c.get(url, headers=headers)
            if r.status_code == 200:
                ctx = r.json()
                if isinstance(ctx, dict):
                    return ctx.get("diagnostic_report") or None
                return None
            log.warning("hub context read for DiagnosticReport %s returned %s",
                        oe_id, r.status_code)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Failed to fetch DiagnosticReport %s via hub: %s", oe_id, e)
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
        parsed = hl7_parse(raw)
        with get_db() as db:
            db.execute(
                "INSERT INTO messages"
                "(direction,msg_type,control_id,sending_app,patient_id,patient_name,raw,status) "
                "VALUES('outbound',?,NULL,'LIS',?,?,?,'sent')",
                (msg_type,
                 patient_id or parsed.get("mrn"),
                 parsed.get("patient_name"),
                 raw),
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


async def consume_loop() -> None:
    """Main consumer loop — runs until the task is cancelled."""
    consumer = BusConsumer(
        redis_url=REDIS_URL,
        group=GROUP,
        consumer=CONSUMER,
        handlers=_HANDLERS,
    )
    await consumer.run()
