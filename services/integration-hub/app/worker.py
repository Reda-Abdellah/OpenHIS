"""
Background polling worker with retry queue and audit log.

Runs a continuous loop every POLL_INTERVAL_S seconds:
  1. Patient sync:      OpenMRS FHIR → OpenELIS FHIR
  2. Lab order routing: OpenMRS ServiceRequest → OpenELIS ServiceRequest
  3. Result routing:    OpenELIS DiagnosticReport → OpenMRS DiagnosticReport

Failed items are placed on a retry queue with exponential back-off
(BASE_BACKOFF_S × 2^(attempt-1), up to MAX_RETRY_ATTEMPTS attempts).
Every event — success or failure — is written to the SQLite audit log.
"""
import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable

import httpx
import redis.asyncio as aioredis

from app.config import POLL_INTERVAL_S, REDIS_URL, MPI_URL
from app.services import openmrs, openelis, odoo
from app.db import audit
from app import bus
from app.token import get_service_token
import app.state as state

log = logging.getLogger("hub.worker")

# Redis-backed dedup sets with 7-day TTL — survive restarts.
# Falls back to in-memory sets when REDIS_URL is not configured.
_DEDUP_TTL = 7 * 24 * 3600   # 7 days in seconds
_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is None and REDIS_URL:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def _dedup_check(key: str, value: str) -> bool:
    """Return True if value is already in the Redis dedup set; add it if not."""
    r = _get_redis()
    if r is None:
        return False   # no Redis — always process (upserts are idempotent)
    full_key = f"hub:dedup:{key}"
    added = await r.sadd(full_key, value)
    await r.expire(full_key, _DEDUP_TTL)
    return added == 0   # 0 means value already existed


def _extract_mrn(fhir_patient: dict) -> str:
    """Extract MRN from FHIR Patient identifier array."""
    for ident in fhir_patient.get("identifier", []):
        type_code = (
            ident.get("type", {})
            .get("coding", [{}])[0]
            .get("code", "")
        )
        if type_code in ("MR", "MRN") or not type_code:
            return ident.get("value", "")
    return ""


async def _sync_to_mpi(fhir_patient: dict, omrs_id: str, oe_id: str | None) -> None:
    """
    Register the patient in MPI and record OpenMRS + OpenELIS crossrefs.
    Best-effort: errors are logged but do not fail the main sync loop.
    """
    mrn = _extract_mrn(fhir_patient)
    if not mrn:
        log.debug("MPI sync skipped for %s — no MRN in FHIR patient", omrs_id)
        return

    name = (fhir_patient.get("name") or [{}])[0]
    given = (name.get("given") or [""])[0]
    family = name.get("family", "")
    payload = {
        "id":        omrs_id,
        "mrn":       mrn,
        "firstname": given,
        "lastname":  family,
        "birthdate": fhir_patient.get("birthDate"),
        "sex":       fhir_patient.get("gender"),
    }

    try:
        token = await get_service_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=5.0) as c:
            # Upsert master patient + register OpenMRS crossref
            r = await c.post(f"{MPI_URL}/api/sync/from-ehr", json=payload, headers=headers)
            if r.status_code not in (200, 201, 202):
                log.warning("MPI sync failed for %s: HTTP %s", omrs_id, r.status_code)
                return
            master_id = r.json().get("master_id")

            # Register OpenELIS crossref if we have it
            if oe_id and master_id:
                await c.post(f"{MPI_URL}/api/crossref", json={
                    "master_id": master_id,
                    "system":    "openelis",
                    "system_id": oe_id,
                    "mrn":       mrn,
                }, headers=headers)
    except Exception as exc:
        log.warning("MPI sync error for %s: %s", omrs_id, exc)

# Retry queue: (next_retry_at, coro_factory, resource_type, resource_id, direction, attempts)
_retry_queue: deque = deque()
MAX_RETRY_ATTEMPTS = 5
BASE_BACKOFF_S     = 15


def _schedule_retry(
    coro_factory: Callable,
    resource_type: str,
    resource_id: str,
    direction: str,
    attempts: int,
) -> None:
    backoff = BASE_BACKOFF_S * (2 ** (attempts - 1))
    _retry_queue.append(
        (time.monotonic() + backoff, coro_factory,
         resource_type, resource_id, direction, attempts)
    )
    log.info(
        "Retry scheduled for %s/%s in %ds (attempt %d)",
        resource_type, resource_id, backoff, attempts,
    )


async def _sync_patients() -> int:
    patients = await openmrs.get_recent_patients()
    count = 0
    for p in patients:
        pid = p.get("id", "")
        if await _dedup_check("patients", pid):
            continue

        # OpenELIS sync — primary; failure triggers retry
        oe_id = None
        try:
            oe_id = await openelis.upsert_patient(p)
            if oe_id:
                count += 1
                await audit.log_event(
                    "patient_synced", "Patient", pid, "omrs→oe", "ok",
                )
                await bus.publish("patient.synced", {
                    "omrs_id": pid,
                    "oe_id": oe_id,
                    "mrn": p.get("identifier", [{}])[0].get("value"),
                })
                await _sync_to_mpi(p, pid, oe_id)
        except Exception as exc:
            await audit.log_event(
                "patient_sync_failed", "Patient", pid, "omrs→oe", "failed",
                str(exc),
            )
            _schedule_retry(
                lambda _p=p: openelis.upsert_patient(_p),
                "Patient", pid, "omrs→oe", 1,
            )

        # Odoo sync — independent; failure does not block OpenELIS or bus publish
        try:
            await odoo.upsert_patient(p)
            await audit.log_event(
                "patient_synced", "Patient", pid, "omrs→odoo", "ok",
            )
        except Exception as exc:
            await audit.log_event(
                "patient_sync_failed", "Patient", pid, "omrs→odoo", "failed",
                str(exc),
            )

    return count


async def _sync_orders() -> int:
    orders = await openmrs.get_active_service_requests()
    count = 0
    for sr in orders:
        oid = sr.get("id", "")
        if await _dedup_check("orders", oid):
            continue
        try:
            oe_id = await openelis.create_service_request(sr)
            if oe_id:
                count += 1
                await audit.log_event(
                    "order_routed", "ServiceRequest", oid, "omrs→oe", "ok",
                )
                await bus.publish("lab_order.routed", {
                    "omrs_id": oid,
                    "oe_id": oe_id,
                })
        except Exception as exc:
            await audit.log_event(
                "order_route_failed", "ServiceRequest", oid, "omrs→oe", "failed",
                str(exc),
            )
            _schedule_retry(
                lambda _sr=sr: openelis.create_service_request(_sr),
                "ServiceRequest", oid, "omrs→oe", 1,
            )
    return count


async def _sync_results() -> int:
    reports = await openelis.get_completed_reports()
    count = 0
    for dr in reports:
        rid = dr.get("id", "")
        if await _dedup_check("reports", rid):
            continue
        try:
            ok = await openmrs.post_diagnostic_report(dr)
            if ok:
                count += 1
                await audit.log_event(
                    "result_routed", "DiagnosticReport", rid, "oe→omrs", "ok",
                )
                await bus.publish("lab_result.ready", {
                    "oe_id": rid,
                    "subject": dr.get("subject", {}).get("reference"),
                })
        except Exception as exc:
            await audit.log_event(
                "result_route_failed", "DiagnosticReport", rid, "oe→omrs", "failed",
                str(exc),
            )
            _schedule_retry(
                lambda _dr=dr: openmrs.post_diagnostic_report(_dr),
                "DiagnosticReport", rid, "oe→omrs", 1,
            )
    return count


async def _drain_retries() -> int:
    """Process all retry items whose back-off delay has elapsed."""
    now = time.monotonic()

    # Split queue into due vs still-pending without mutating during iteration
    due, pending = [], []
    while _retry_queue:
        item = _retry_queue.popleft()
        (due if item[0] <= now else pending).append(item)
    _retry_queue.extend(pending)

    processed = 0
    for (_, coro_factory, resource_type, resource_id, direction, attempts) in due:
        try:
            await coro_factory()
            await audit.log_event(
                "retry_ok", resource_type, resource_id, direction, "ok",
                attempts=attempts,
            )
            processed += 1
        except Exception as exc:
            if attempts < MAX_RETRY_ATTEMPTS:
                _schedule_retry(
                    coro_factory, resource_type, resource_id, direction, attempts + 1,
                )
                await audit.log_event(
                    "retry_rescheduled", resource_type, resource_id, direction,
                    "retry_scheduled", str(exc), attempts=attempts,
                )
            else:
                state.errors += 1
                await audit.log_event(
                    "retry_exhausted", resource_type, resource_id, direction,
                    "failed", str(exc), attempts=attempts,
                )
    return processed


async def poll_once() -> dict:
    """Run one full sync cycle. Returns a summary dict."""
    patients = await _sync_patients()
    orders   = await _sync_orders()
    results  = await _sync_results()
    retried  = await _drain_retries()
    summary  = {
        "patients": patients, "orders": orders,
        "results": results,   "retried": retried,
    }
    log.info("Poll cycle done — %s", summary)
    return summary


async def poll_loop() -> None:
    """Main background task — runs forever, sleeps between cycles."""
    log.info("Worker started (interval=%ds)", POLL_INTERVAL_S)
    while True:
        try:
            summary = await poll_once()
            state.patients_synced += summary["patients"]
            state.orders_synced   += summary["orders"]
            state.reports_synced  += summary["results"]
            state.last_poll_at     = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            state.errors += 1
            log.error("Poll cycle error: %s", e)
            await audit.log_event("poll_error", status="failed", detail=str(e))
        await asyncio.sleep(POLL_INTERVAL_S)
